# IMPORT ALL PACKAGE

import os
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader, random_split
import torch.optim as optim

from pytorchvideo.transforms import (
    Normalize,
    RandomShortSideScale,
    ShortSideScale,
    UniformTemporalSubsample
)
from torchvision.transforms import Compose, Lambda, CenterCrop
from torchvision.transforms._transforms_video import (
    RandomCrop,
    RandomHorizontalFlipVideo
)
import cv2
import numpy as np
from tqdm import tqdm
import matplotlib.pyplot as plt
from sklearn.model_selection import train_test_split



# Set random seed for reproducibility
torch.manual_seed(42)
np.random.seed(42)


# Custom Dataset Class
class CustomActionDataset(Dataset):
    def __init__(self, video_paths, labels, transform=None, num_frames=4):
        """
        video_paths: List of paths to video files
        labels: List of corresponding labels
        transform: Optional transform to be applied on video
        num_frames: Number of frames to sample from each video
        """
        self.video_paths = video_paths
        self.labels = labels
        self.transform = transform
        self.num_frames = num_frames

    def load_video(self, video_path):
        frames = []
        cap = cv2.VideoCapture(video_path)
        
        # Get total frames
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        indices = np.linspace(0, total_frames-1, self.num_frames, dtype=int)
        
        for frame_idx in range(total_frames):
            ret, frame = cap.read()
            if not ret:
                break
                
            if frame_idx in indices:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frame = cv2.resize(frame, (182, 182))
                frames.append(frame)
                
        cap.release()
        return np.array(frames)

    def __len__(self):
        return len(self.video_paths)

    def __getitem__(self, idx):
        video_path = self.video_paths[idx]
        label = self.labels[idx]
        
        frames = self.load_video(video_path)
        
        # Convert to float32 and normalize to [0, 1]
        frames = frames.astype(np.float32) / 255.0
        
        # Convert to tensor [T, H, W, C] -> [C, T, H, W]
        video = torch.from_numpy(frames).permute(3, 0, 1, 2)
        
        if self.transform:
            video = self.transform(video)
            
        return video, label


# Function to collect dataset
def collect_dataset(root_dir):
    video_paths = []
    labels = []
    class_to_idx = {'Walking': 0, 'Standing Still': 1, 'Sitting': 2, 'Drinking': 3, 'Eating': 4}
    
    for action in class_to_idx.keys():
        action_dir = os.path.join(root_dir, action)
        if not os.path.exists(action_dir):
            continue
            
        for video_file in os.listdir(action_dir):
            if video_file.endswith(('.mp4', '.avi')):
                video_paths.append(os.path.join(action_dir, video_file))
                labels.append(class_to_idx[action])
                
    return video_paths, labels



# Modify X3D head
def modify_x3d_head(model, num_classes=5):
    """Modify the classification head of X3D model with dropout and freeze/unfreeze layers"""
    # 1. Freeze all layers first
    for param in model.parameters():
        param.requires_grad = False
    
    # 2. Get input features from the last block
    in_features = model.blocks[5].proj.in_features
    
    # 3. Modify the head with new number of classes
    model.blocks[5].proj = nn.Sequential(
        nn.Dropout(p=0.5),
        nn.Linear(in_features, num_classes)
    )
    
    # 4. Unfreeze just the modified head (block 5)
    for param in model.blocks[5].parameters():
        param.requires_grad = True
        
    return model



# Training function
def train_epoch(model, train_loader, criterion, optimizer, device):
    model.train()
    running_loss = 0.0
    correct = 0
    total = 0
    
    for videos, labels in tqdm(train_loader, desc='Training'):
        videos, labels = videos.to(device), labels.to(device)
        
        optimizer.zero_grad()
        outputs = model(videos)
        loss = criterion(outputs, labels)
        loss.backward()
        optimizer.step()
        
        running_loss += loss.item()
        _, predicted = outputs.max(1)
        total += labels.size(0)
        correct += predicted.eq(labels).sum().item()
    
    epoch_loss = running_loss / len(train_loader)
    epoch_acc = 100. * correct / total
    return epoch_loss, epoch_acc


# Validation function
def validate(model, val_loader, criterion, device):
    model.eval()
    running_loss = 0.0
    correct = 0
    total = 0
    
    with torch.no_grad():
        for videos, labels in tqdm(val_loader, desc='Validation'):
            videos, labels = videos.to(device), labels.to(device)
            outputs = model(videos)
            loss = criterion(outputs, labels)
            
            running_loss += loss.item()
            _, predicted = outputs.max(1)
            total += labels.size(0)
            correct += predicted.eq(labels).sum().item()
    
    epoch_loss = running_loss / len(val_loader)
    epoch_acc = 100. * correct / total
    return epoch_loss, epoch_acc



# Plot training history
def plot_training_history(train_losses, val_losses, train_accs, val_accs):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 5))
    
    # Plot losses
    ax1.plot(train_losses, label='Train Loss')
    ax1.plot(val_losses, label='Val Loss')
    ax1.set_xlabel('Epoch')
    ax1.set_ylabel('Loss')
    ax1.set_title('Training and Validation Loss')
    ax1.legend()
    
    # Plot accuracies
    ax2.plot(train_accs, label='Train Acc')
    ax2.plot(val_accs, label='Val Acc')
    ax2.set_xlabel('Epoch')
    ax2.set_ylabel('Accuracy (%)')
    ax2.set_title('Training and Validation Accuracy')
    ax2.legend()
    
    plt.tight_layout()
    plt.show()

def main():
    # Configuration
    DATA_ROOT = "/home/sophic/Video_AI_Project/Dataset/Human Activity Recognition - Video Dataset/" # Update this path
    BATCH_SIZE = 32
    NUM_EPOCHS = 20
    TRAIN_SIZE = 0.7
    VAL_SIZE = 0.15
    TEST_SIZE = 0.15
    
    # Device configuration
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Using device: {device}")
    
    # Model parameters
    model_name = 'x3d_xs'
    transform_params = {
        "side_size": 182,
        "crop_size": 182,
        "num_frames": 4,
        "sampling_rate": 12,
    }
    
    # Collect dataset
    video_paths, labels = collect_dataset(DATA_ROOT)
    
    # Split dataset
    train_paths, temp_paths, train_labels, temp_labels = train_test_split(
        video_paths, labels, train_size=TRAIN_SIZE, stratify=labels, random_state=42
    )
    
    # Further split temp into validation and test
    val_ratio = VAL_SIZE / (VAL_SIZE + TEST_SIZE)
    val_paths, test_paths, val_labels, test_labels = train_test_split(
        temp_paths, temp_labels, train_size=val_ratio, stratify=temp_labels, random_state=42
    )
    
    print(f"Train size: {len(train_paths)}")
    print(f"Validation size: {len(val_paths)}")
    print(f"Test size: {len(test_paths)}")
    
    # Define transforms
    train_transform = Compose([
        UniformTemporalSubsample(transform_params["num_frames"]),
        Normalize([0.45, 0.45, 0.45], [0.225, 0.225, 0.225]),
        RandomShortSideScale(min_size=256, max_size=320),
        RandomCrop(transform_params["crop_size"]),
        RandomHorizontalFlipVideo(p = 0.5)
    ])
    
    val_transform = Compose([
        UniformTemporalSubsample(transform_params["num_frames"]),
        Normalize([0.45, 0.45, 0.45], [0.225, 0.225, 0.225]),
        ShortSideScale(size=transform_params["side_size"]),
        CenterCrop(transform_params["crop_size"])
        # Lambda(lambda x: x/255.0),
    ])
    
    
    # Create datasets
    train_dataset = CustomActionDataset(
        train_paths, train_labels,
        transform=train_transform,
        num_frames=transform_params["num_frames"]
    )
    
    val_dataset = CustomActionDataset(
        val_paths, val_labels,
        transform=val_transform,
        num_frames=transform_params["num_frames"]
    )
    
    test_dataset = CustomActionDataset(
        test_paths, test_labels,
        transform=val_transform,
        num_frames=transform_params["num_frames"]
    )
    
    # Create data loaders
    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True, num_workers=4)
    val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE, shuffle=False, num_workers=4)
    test_loader = DataLoader(test_dataset, batch_size=BATCH_SIZE, shuffle=False, num_workers=4)
    
    # Load and modify model
    model = torch.hub.load('facebookresearch/pytorchvideo', model_name, pretrained=True)
    print(model)
    model = modify_x3d_head(model, num_classes=5)
    print("Modify Model")
    print(model)
    model = model.to(device)
    
    
    # Define loss and optimizer
    criterion = nn.CrossEntropyLoss()
    # optimizer = optim.Adam(model.parameters(), lr=0.0001)
    optimizer = optim.AdamW(model.parameters(), lr=0.0001, weight_decay=0.05) 
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min', patience=3, factor=0.1) 
    
    # Training history
    train_losses = []
    val_losses = []
    train_accs = []
    val_accs = []
    best_val_loss = float('inf')
    
    # Training loop
    for epoch in range(NUM_EPOCHS):
        print(f'\nEpoch {epoch+1}/{NUM_EPOCHS}')
        
        # Train
        train_loss, train_acc = train_epoch(model, train_loader, criterion, optimizer, device)
        train_losses.append(train_loss)
        train_accs.append(train_acc)
        
        # Validate
        val_loss, val_acc = validate(model, val_loader, criterion, device)
        val_losses.append(val_loss)
        val_accs.append(val_acc)
        
        # Print metrics
        print(f'Train Loss: {train_loss:.4f} | Train Acc: {train_acc:.2f}%')
        print(f'Val Loss: {val_loss:.4f} | Val Acc: {val_acc:.2f}%')
        
        # Learning rate scheduling
        scheduler.step(val_loss)
        
        # Save best model
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            torch.save(model.state_dict(), 'best_x3d_model(4frames).pth')
    
    # Plot training history
    plot_training_history(train_losses, val_losses, train_accs, val_accs)
    
    # Evaluate on test set
    test_loss, test_acc = validate(model, test_loader, criterion, device)
    print(f'\nTest Loss: {test_loss:.4f} | Test Acc: {test_acc:.2f}%')



# Run training
if __name__ == "__main__":
    main()


