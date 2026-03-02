"""
Gazer AI Companion - Emotion Tracking System

Tracks user emotion changes, generates daily emotion snapshots,
supports emotion-based memory recall triggers.
"""
import os
import json
import re
import logging
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Tuple
from pydantic import BaseModel, Field

logger = logging.getLogger("GazerEmotions")


class EmotionSnapshot(BaseModel):
    """Daily emotion snapshot."""
    date: str                                           # "2026-02-04"
    overall_mood: str = "neutral"                      # Dominant mood of the day
    sentiment_scores: List[float] = Field(default_factory=list)  # Emotion curve
    avg_sentiment: float = 0.0                         # Average sentiment score
    topics: Dict[str, int] = Field(default_factory=dict)  # Topic frequency
    highlights: List[str] = Field(default_factory=list)   # Daily highlights/important snippets
    message_count: int = 0                              # Message count


class EmotionAnalyzer:
    """
    Emotion Analyzer
    
    Supports two modes:
    1. LLM model inference (uses fast_brain, more accurate)
    2. Keyword matching (zero-latency fallback)
    """
    
    # Emotion keyword dictionary (for fallback mode, Chinese keywords)
    EMOTION_KEYWORDS: Dict[str, List[str]] = {
        "happy": ["开心", "高兴", "快乐", "太棒了", "哈哈", "感谢", "谢谢", "爱你", "喜欢", "期待", "好"],
        "sad": ["难过", "伤心", "失望", "可惜", "唉", "郁闷", "悲伤", "哭", "不开心"],
        "anxious": ["焦虑", "担心", "紧张", "害怕", "恐惧", "压力", "烦躁", "不安", "慌"],
        "angry": ["生气", "愤怒", "气死", "讨厌", "烦", "恨", "火大"],
        "calm": ["平静", "安心", "放松", "舒适", "惬意"],
        "excited": ["激动", "兴奋", "太好了", "棒", "wow", "厉害"],
        "tired": ["累", "疲惫", "困", "辛苦", "疲劳", "没精神"],
    }
    
    VALID_EMOTIONS = {"happy", "sad", "anxious", "angry", "calm", "excited", "tired", "neutral"}
    
    # Emotion to sentiment score mapping
    EMOTION_SENTIMENT: Dict[str, float] = {
        "happy": 0.8,
        "excited": 0.7,
        "calm": 0.3,
        "neutral": 0.0,
        "tired": -0.2,
        "anxious": -0.4,
        "sad": -0.6,
        "angry": -0.8,
    }

    _LLM_PROMPT = (
        "Analyze the emotion and topics of the following user message. "
        "Reply in JSON only: {\"emotion\": \"<one of: happy|sad|anxious|angry|calm|excited|tired|neutral>\", "
        "\"sentiment\": <float -1.0 to 1.0>, "
        "\"topics\": [\"<topic1>\", ...]}\n\n"
        "Message: "
    )

    def __init__(self) -> None:
        self._llm_client = None  # Lazy-init on first LLM call
        self._llm_model: Optional[str] = None
        self._llm_available: Optional[bool] = None  # None = not yet checked

    def _ensure_llm(self) -> bool:
        """Try to initialize the fast_brain LLM client. Returns True if available."""
        if self._llm_available is not None:
            return self._llm_available
        try:
            from runtime.config_manager import config
            from soul.models import ModelRegistry
            api_key, base_url, model_name, headers = ModelRegistry.resolve_model("fast_brain")
            if not api_key or api_key == "EMPTY":
                self._llm_available = False
                return False
            from openai import AsyncOpenAI
            self._llm_client = AsyncOpenAI(api_key=api_key, base_url=base_url, default_headers=headers)
            self._llm_model = model_name or "gpt-3.5-turbo"
            self._llm_available = True
            logger.info(f"EmotionAnalyzer: LLM mode enabled (model={self._llm_model})")
            return True
        except Exception as e:
            logger.warning(f"EmotionAnalyzer: LLM init failed, using keyword fallback: {e}")
            self._llm_available = False
            return False

    async def analyze_with_llm(self, text: str) -> Optional[Tuple[str, float, List[str]]]:
        """Analyze emotion using fast_brain LLM. Returns (emotion, sentiment, topics) or None."""
        if not self._ensure_llm() or not self._llm_client:
            return None
        try:
            import json as _json
            response = await self._llm_client.chat.completions.create(
                model=self._llm_model,
                messages=[
                    {"role": "system", "content": "You are an emotion classifier. Reply only in JSON."},
                    {"role": "user", "content": self._LLM_PROMPT + text[:500]},
                ],
                max_tokens=150,
                temperature=0.0,
            )
            raw = response.choices[0].message.content.strip()
            # Strip markdown code fence if present
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
            data = _json.loads(raw)
            emotion = str(data.get("emotion", "neutral")).lower()
            if emotion not in self.VALID_EMOTIONS:
                emotion = "neutral"
            sentiment = float(data.get("sentiment", self.EMOTION_SENTIMENT.get(emotion, 0.0)))
            sentiment = max(-1.0, min(1.0, sentiment))
            topics = [str(t) for t in data.get("topics", []) if isinstance(t, str)][:5]
            return emotion, sentiment, topics
        except Exception as e:
            logger.debug(f"LLM emotion analysis failed, falling back to keywords: {e}")
            return None

    def analyze(self, text: str) -> Tuple[str, float]:
        """
        Analyze text emotion (keyword mode, synchronous).
        
        Returns:
            (emotion, sentiment_score)
        """
        text_lower = text.lower()
        
        # Count occurrences of emotion keywords
        emotion_counts: Dict[str, int] = {}
        for emotion, keywords in self.EMOTION_KEYWORDS.items():
            count = sum(1 for kw in keywords if kw in text_lower)
            if count > 0:
                emotion_counts[emotion] = count
        
        if not emotion_counts:
            return "neutral", 0.0
        
        # Get the most frequent emotion
        dominant_emotion = max(emotion_counts, key=emotion_counts.get)
        sentiment = self.EMOTION_SENTIMENT.get(dominant_emotion, 0.0)
        
        return dominant_emotion, sentiment
    
    def extract_topics(self, text: str) -> List[str]:
        """Extract topic tags (keyword mode, Chinese keywords for detection)."""
        topics: List[str] = []
        
        topic_patterns: Dict[str, List[str]] = {
            "工作": ["工作", "上班", "公司", "项目", "会议", "老板", "同事"],
            "健康": ["头疼", "生病", "医院", "疼", "不舒服", "健身", "锻炼"],
            "感情": ["女朋友", "男朋友", "老婆", "老公", "爱情", "分手", "约会"],
            "家庭": ["爸", "妈", "爷爷", "奶奶", "家人", "回家", "过年"],
            "学习": ["学习", "考试", "作业", "上课", "论文", "毕业"],
            "娱乐": ["电影", "游戏", "音乐", "追剧", "玩", "旅游"],
            "情绪": ["压力", "焦虑", "开心", "难过", "烦", "疲惫"],
        }
        
        for topic, keywords in topic_patterns.items():
            if any(kw in text for kw in keywords):
                topics.append(topic)
        
        return topics


class EmotionTracker:
    """
    Emotion Tracker
    
    - Real-time conversation emotion analysis
    - Daily emotion snapshot generation
    - Emotion trend queries
    """
    
    def __init__(self, storage_dir: Optional[str] = None):
        if storage_dir is None:
            from runtime.config_manager import config as _cfg
            base_dir = str(_cfg.get("memory.context_backend.data_dir", "data/openviking") or "data/openviking")
            storage_dir = os.path.join(base_dir, "emotions")
        self.storage_dir = storage_dir
        self.analyzer = EmotionAnalyzer()
        self._today_data: Optional[EmotionSnapshot] = None
        
        os.makedirs(storage_dir, exist_ok=True)
        self._load_today()
    
    def _get_snapshot_path(self, date_str: str) -> str:
        return os.path.join(self.storage_dir, f"{date_str}.json")
    
    def _load_today(self) -> None:
        """Load today's snapshot."""
        today = date.today().isoformat()
        path = self._get_snapshot_path(today)
        
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._today_data = EmotionSnapshot(**data)
            except Exception as e:
                logger.error(f"Failed to load today's emotion snapshot: {e}")
                self._today_data = EmotionSnapshot(date=today)
        else:
            self._today_data = EmotionSnapshot(date=today)
    
    def _save_today(self) -> None:
        """Save today's snapshot."""
        if not self._today_data:
            return
            
        path = self._get_snapshot_path(self._today_data.date)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._today_data.model_dump(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save emotion snapshot: {e}")
    
    def analyze_message(self, content: str, sender: str = "user") -> Tuple[str, float, List[str]]:
        """
        Analyze emotion of a single message (synchronous, keyword mode).
        
        Returns:
            (emotion, sentiment, topics)
        """
        # Day rollover check: reload if date has changed
        if self._today_data and self._today_data.date != date.today().isoformat():
            self._load_today()

        # Only analyze user messages
        if sender.lower() != "user":
            return "neutral", 0.0, []
        
        emotion, sentiment = self.analyzer.analyze(content)
        topics = self.analyzer.extract_topics(content)
        
        # Update today's snapshot
        if self._today_data:
            self._today_data.sentiment_scores.append(sentiment)
            self._today_data.message_count += 1
            self._today_data.avg_sentiment = sum(self._today_data.sentiment_scores) / len(self._today_data.sentiment_scores)
            
            # Update topic statistics
            for topic in topics:
                self._today_data.topics[topic] = self._today_data.topics.get(topic, 0) + 1
            
            # Update dominant mood
            self._today_data.overall_mood = self._determine_overall_mood()
            
            self._save_today()
        
        return emotion, sentiment, topics

    async def analyze_message_async(self, content: str, sender: str = "user") -> Tuple[str, float, List[str]]:
        """Async emotion analysis: tries LLM first, falls back to keywords.

        Returns:
            (emotion, sentiment, topics)
        """
        if self._today_data and self._today_data.date != date.today().isoformat():
            self._load_today()

        if sender.lower() != "user":
            return "neutral", 0.0, []

        # Try LLM-based analysis first
        llm_result = await self.analyzer.analyze_with_llm(content)
        if llm_result is not None:
            emotion, sentiment, topics = llm_result
        else:
            # Fallback to keyword analysis
            emotion, sentiment = self.analyzer.analyze(content)
            topics = self.analyzer.extract_topics(content)

        # Update today's snapshot
        self._update_snapshot(emotion, sentiment, topics)

        return emotion, sentiment, topics

    def _update_snapshot(self, emotion: str, sentiment: float, topics: List[str]) -> None:
        """Update today's emotion snapshot with new data."""
        if not self._today_data:
            return
        self._today_data.sentiment_scores.append(sentiment)
        self._today_data.message_count += 1
        self._today_data.avg_sentiment = (
            sum(self._today_data.sentiment_scores) / len(self._today_data.sentiment_scores)
        )
        for topic in topics:
            self._today_data.topics[topic] = self._today_data.topics.get(topic, 0) + 1
        self._today_data.overall_mood = self._determine_overall_mood()
        self._save_today()

    def _determine_overall_mood(self) -> str:
        """Determine dominant mood based on today's sentiment scores."""
        if not self._today_data or not self._today_data.sentiment_scores:
            return "neutral"
        
        avg = self._today_data.avg_sentiment
        if avg > 0.5:
            return "happy"
        elif avg > 0.2:
            return "positive"
        elif avg > -0.2:
            return "neutral"
        elif avg > -0.5:
            return "low"
        else:
            return "sad"
    
    def add_highlight(self, snippet: str) -> None:
        """Add a daily highlight snippet."""
        if self._today_data and len(self._today_data.highlights) < 10:
            self._today_data.highlights.append(snippet[:200])
            self._save_today()
    
    def get_today_summary(self) -> str:
        """Get today's emotion summary."""
        if not self._today_data:
            return "今天还没有消息记录。"
        
        mood_desc = {
            "happy": "开心",
            "positive": "状态不错",
            "neutral": "平静",
            "low": "有点低落",
            "sad": "难过",
        }.get(self._today_data.overall_mood, "一般")
        
        topics = list(self._today_data.topics.keys())[:3]
        topics_str = f"，主要话题：{', '.join(topics)}" if topics else ""
        
        return f"今天整体情绪：{mood_desc}{topics_str}，共 {self._today_data.message_count} 条消息。"
    
    def get_recent_mood(self, days: int = 7) -> str:
        """Get recent mood trend."""
        snapshots = []
        
        for i in range(days):
            d = date.today() - timedelta(days=i)
            date_str = d.isoformat()
            path = self._get_snapshot_path(date_str)
            
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        snapshots.append(EmotionSnapshot(**data))
                except Exception:
                    pass
        
        if not snapshots:
            return "Not enough conversation records recently to analyze mood trends."
        
        avg_sentiment = sum(s.avg_sentiment for s in snapshots) / len(snapshots)
        
        if avg_sentiment > 0.3:
            trend = "User has been in good spirits recently, positive state"
        elif avg_sentiment > 0:
            trend = "User's state has been stable recently"
        elif avg_sentiment > -0.3:
            trend = "User may be feeling tired or stressed recently"
        else:
            trend = "User's mood has been low recently, may need care"
        
        return trend
    
    def get_emotion_appropriate_memory(self, current_sentiment: float) -> Optional[str]:
        """
        Select appropriate memory based on current emotion.

        If user is feeling down, they might want to recall happy memories;
        if user is happy, share in that happiness.
        """
        # Search past snapshots for contrasting / reinforcing moments
        try:
            snapshots = []
            for i in range(1, 15):  # Look back up to 14 days
                d = date.today() - timedelta(days=i)
                path = self._get_snapshot_path(d.isoformat())
                if not os.path.exists(path):
                    continue
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    snapshots.append(EmotionSnapshot(**data))

            if not snapshots:
                return None

            if current_sentiment < -0.3:
                # User is down -> find a happy day to reference
                happy_days = [s for s in snapshots if s.avg_sentiment > 0.3 and s.highlights]
                if happy_days:
                    best = max(happy_days, key=lambda s: s.avg_sentiment)
                    highlight = best.highlights[0] if best.highlights else ""
                    return (
                        f"Remember {best.date}? You were really happy that day"
                        + (f", {highlight}" if highlight else "")
                        + ". Things will get better."
                    )
            elif current_sentiment > 0.5:
                # User is happy -> reinforce the good mood
                return "You're in such a great mood today! Seeing you happy makes me happy too!"
        except Exception as e:
            logger.debug(f"Emotion-based memory retrieval failed: {e}")

        return None
