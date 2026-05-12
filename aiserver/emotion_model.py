# emotion_model.py

import torch
import torch.nn as nn
import timm

# 실제 학습된 클래스 (emotion_model_v3.pth 기준 — 4-class)
TRAINED_CLASSES = ["neutral", "negative", "positive", "surprise"]
NUM_TRAINED     = len(TRAINED_CLASSES)   # 4

# 프로젝트 전체 클래스 (추론 결과 매핑용)
EMOTION_CLASSES = ["neutral", "negative", "positive", "confusion", "surprise"]

FEAT_DIM = 1408
IMG_SIZE = 260


class EmotionModel(nn.Module):
    """
    실제 학습된 구조:
      EfficientNet-B2 백본
      → head.0 LayerNorm(1408)
      → head.1 Dropout
      → head.2 Linear(1408 → 512)
      → head.3 ReLU
      → head.4 Dropout
      → head.5 Linear(512 → 3)   # negative / positive / surprise
    """
    def __init__(self, num_classes=NUM_TRAINED, dropout=0.3):
        super().__init__()
        self.backbone = timm.create_model(
            "efficientnet_b2",
            pretrained=False,
            num_classes=0,
            global_pool="avg",
        )
        self.head = nn.Sequential(
            nn.LayerNorm(FEAT_DIM),       # head.0
            nn.Dropout(dropout),          # head.1
            nn.Linear(FEAT_DIM, 512),     # head.2
            nn.ReLU(),                    # head.3
            nn.Dropout(dropout),          # head.4
            nn.Linear(512, num_classes),  # head.5
        )

    def forward(self, x):
        """
        x: (B, seq_len, C, H, W) 또는 (B, C, H, W)
        반환: (B, seq_len, 3) 또는 (B, 3)
        """
        if x.dim() == 5:
            B, T, C, H, W = x.shape
            feats = self.backbone(x.view(B * T, C, H, W))
            feats = feats.view(B, T, FEAT_DIM)
            return self.head(feats)
        else:
            return self.head(self.backbone(x))


def load_model(model_path: str, device: torch.device) -> EmotionModel:
    """가중치 로드 후 eval 모드 반환"""
    model = EmotionModel()
    state = torch.load(model_path, map_location=device, weights_only=True)
    model.load_state_dict(state)
    model.to(device)
    model.eval()
    return model


def predict_emotion(logits: torch.Tensor) -> list[dict]:
    """
    logits: (seq_len, 3)
    TRAINED_CLASSES 기준으로 감정 + 확신도 반환
    confidence < 0.5이고 negative이면 neutral로 후처리
    """
    import torch.nn.functional as F
    probs   = F.softmax(logits, dim=-1)
    results = []
    for prob in probs:
        idx        = int(prob.argmax())
        confidence = float(prob[idx])
        emotion    = TRAINED_CLASSES[idx]
        if confidence < 0.5 and emotion == "negative":
            emotion = "neutral"
        results.append({"emotion": emotion, "confidence": round(confidence, 4)})
    return results
