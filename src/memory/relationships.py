"""
Gazer AI Companion - Relationship Graph Management

Tracks people mentioned by the user and their relationships,
supports relationship context injection during natural recall.
"""
import os
import json
import re
import logging
from datetime import datetime
from typing import List, Dict, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger("GazerRelationships")


class Person(BaseModel):
    """Person entity mentioned by the user."""
    name: str                          # Primary name
    aliases: List[str] = Field(default_factory=list)  # Alternative names
    relationship: str = "unknown"      # Relationship type: family/friend/colleague/romantic/other
    first_mentioned: datetime = Field(default_factory=datetime.now)
    last_mentioned: datetime = Field(default_factory=datetime.now)
    mention_count: int = 1
    context_snippets: List[str] = Field(default_factory=list)  # Related conversation snippets (max 5)
    sentiment: float = 0.0             # User's sentiment towards this person (-1.0 ~ 1.0)
    notes: str = ""                    # Notes
    
    def update_mention(self, context: str, sentiment_delta: float = 0.0):
        """Update mention information."""
        self.last_mentioned = datetime.now()
        self.mention_count += 1
        # Keep last 5 context snippets
        self.context_snippets.append(context[:200])
        if len(self.context_snippets) > 5:
            self.context_snippets = self.context_snippets[-5:]
        # Sliding average for sentiment
        self.sentiment = self.sentiment * 0.8 + sentiment_delta * 0.2


class RelationshipGraph:
    """
    Relationship Graph Manager
    
    Maintains all people mentioned by the user and their relationships, supports:
    - Automatic extraction of people from conversations
    - Updating person information
    - Generating relationship context for LLM injection
    """
    
    # Common relationship word patterns (Chinese)
    RELATIONSHIP_PATTERNS = {
        "family": [
            r"我(的)?(?:[^,，。！？]{0,5})(妈妈|母亲|老妈|妈)",
            r"我(的)?(?:[^,，。！？]{0,5})(爸爸|父亲|老爸|爸)",
            r"我(的)?(?:[^,，。！？]{0,5})(姐姐|妹妹|哥哥|弟弟|姐|妹|哥|弟)",
            r"我(的)?(?:[^,，。！？]{0,5})(爷爷|奶奶|姥姥|姥爷|外公|外婆)",
            r"我(的)?(?:[^,，。！？]{0,5})(儿子|女儿|孩子)",
        ],
        "romantic": [
            r"我(的)?(?:[^,，。！？]{0,5})(女朋友|男朋友|女友|男友|对象)",
            r"我(的)?(?:[^,，。！？]{0,5})(老婆|老公|妻子|丈夫|爱人)",
        ],
        "friend": [
            r"我(的)?(?:[^,，。！？]{0,5})朋友",
            r"我(的)?(?:[^,，。！？]{0,5})(闺蜜|哥们|兄弟|死党)",
        ],
        "colleague": [
            r"我(的)?(?:[^,，。！？]{0,5})(老板|领导|上司|经理|主管)",
            r"我(的)?(?:[^,，。！？]{0,5})(同事|同学|室友)",
        ],
    }
    
    def __init__(self, storage_path: Optional[str] = None):
        if storage_path is None:
            from runtime.config_manager import config as _cfg
            base_dir = str(_cfg.get("memory.context_backend.data_dir", "data/openviking") or "data/openviking")
            storage_path = os.path.join(base_dir, "RELATIONSHIPS.md")
        self.storage_path = storage_path
        self.people: Dict[str, Person] = {}
        self._load()
    
    def _load(self):
        """Load relationship graph from file."""
        if not os.path.exists(self.storage_path):
            return
        
        try:
            # Try to read JSON format backup
            json_path = self.storage_path.replace(".md", ".json")
            if os.path.exists(json_path):
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for name, person_data in data.items():
                        try:
                            # 转换日期字符串
                            person_data["first_mentioned"] = datetime.fromisoformat(person_data["first_mentioned"])
                            person_data["last_mentioned"] = datetime.fromisoformat(person_data["last_mentioned"])
                            self.people[name] = Person(**person_data)
                        except Exception as e:
                            logger.warning(f"Skipping corrupt relationship entry '{name}': {e}")
                logger.info(f"Loaded {len(self.people)} relationships from {json_path}")
        except Exception as e:
            logger.error(f"Failed to load relationships: {e}")
    
    def _save(self):
        """Save relationship graph to file."""
        try:
            # Save JSON format (for program reading)
            json_path = self.storage_path.replace(".md", ".json")
            os.makedirs(os.path.dirname(json_path), exist_ok=True)
            
            data = {}
            for name, person in self.people.items():
                person_dict = person.model_dump()
                person_dict["first_mentioned"] = person.first_mentioned.isoformat()
                person_dict["last_mentioned"] = person.last_mentioned.isoformat()
                data[name] = person_dict
            
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            # Also save Markdown format (for human reading)
            self._save_markdown()
            
        except Exception as e:
            logger.error(f"Failed to save relationships: {e}")
    
    def _save_markdown(self):
        """Save human-readable Markdown format."""
        lines = ["# User Relationship Graph\n"]
        lines.append(f"*Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n\n")
        
        # Group by relationship type
        by_type: Dict[str, List[Person]] = {}
        for person in self.people.values():
            rel_type = person.relationship
            if rel_type not in by_type:
                by_type[rel_type] = []
            by_type[rel_type].append(person)
        
        type_labels = {
            "family": "👨‍👩‍👧 Family",
            "romantic": "💕 Partner",
            "friend": "🤝 Friends",
            "colleague": "💼 Colleagues",
            "other": "📌 Other",
            "unknown": "❓ Uncategorized",
        }
        
        for rel_type, people in by_type.items():
            label = type_labels.get(rel_type, rel_type)
            lines.append(f"## {label}\n\n")
            for p in sorted(people, key=lambda x: x.mention_count, reverse=True):
                aliases_str = f" ({', '.join(p.aliases)})" if p.aliases else ""
                sentiment_emoji = "😊" if p.sentiment > 0.3 else "😐" if p.sentiment > -0.3 else "😟"
                lines.append(f"- **{p.name}**{aliases_str} {sentiment_emoji}\n")
                lines.append(f"  - Mentions: {p.mention_count}\n")
                if p.notes:
                    lines.append(f"  - Notes: {p.notes}\n")
            lines.append("\n")
        
        with open(self.storage_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    
    def add_or_update_person(self, name: str, relationship: str = "unknown", 
                             alias: Optional[str] = None, context: str = "",
                             sentiment: float = 0.0) -> Person:
        """Add or update a person."""
        name = name.strip()
        
        if name in self.people:
            person = self.people[name]
            person.update_mention(context, sentiment)
            if alias and alias not in person.aliases:
                person.aliases.append(alias)
            if relationship != "unknown" and person.relationship == "unknown":
                person.relationship = relationship
        else:
            person = Person(
                name=name,
                relationship=relationship,
                aliases=[alias] if alias else [],
                context_snippets=[context[:200]] if context else [],
                sentiment=sentiment
            )
            self.people[name] = person
        
        self._save()
        return person
    
    def get_person(self, name: str) -> Optional[Person]:
        """Get person information."""
        return self.people.get(name)
    
    def find_by_alias(self, alias: str) -> Optional[Person]:
        """Find a person by alias."""
        for person in self.people.values():
            if alias in person.aliases or alias == person.name:
                return person
        return None
    
    def extract_people_from_text(self, text: str) -> List[Dict[str, str]]:
        """
        Extract mentioned people from text.
        
        Returns:
            List of {"name": str, "relationship": str, "alias": Optional[str]}
        """
        extracted = []
        
        # Check known people
        for name in self.people.keys():
            if name in text:
                extracted.append({
                    "name": name,
                    "relationship": self.people[name].relationship,
                    "alias": None
                })
        
        # Check relationship patterns
        for rel_type, patterns in self.RELATIONSHIP_PATTERNS.items():
            for pattern in patterns:
                # A. Find relationship followed by name
                after_pattern = pattern + r"[,，]?\s*(?P<person_name>[^\s,，。！？]{1,4})"
                after_matches = re.finditer(after_pattern, text)
                for f_match in after_matches:
                    name = f_match.group("person_name")
                    if name is not None and len(name) >= 1:
                        extracted.append({
                            "name": name,
                            "relationship": rel_type,
                            "alias": f_match.group(0)  # Full match as alias reference
                        })
                
                # B. Find name followed by relationship
                before_pattern = r"([^\s,，。！？]{1,4})\s*[是为]?\s*(?:很)?\s*" + pattern
                before_matches = re.finditer(before_pattern, text)
                for f_match in before_matches:
                    name = f_match.group(1) # 第一个组是名字
                    if name and len(name) >= 1:
                        extracted.append({
                            "name": name,
                            "relationship": rel_type,
                            "alias": f_match.group(0)
                        })
        
        return extracted
    
    def update_from_message(self, content: str, sentiment: float = 0.0):
        """Automatically extract and update person relationships from a message."""
        people = self.extract_people_from_text(content)
        for p in people:
            self.add_or_update_person(
                name=p["name"],
                relationship=p["relationship"],
                alias=p.get("alias"),
                context=content,
                sentiment=sentiment
            )
    
    def to_context(self, max_people: int = 5) -> str:
        """
        Generate relationship context for injection into LLM prompt.
        
        Only includes the most recently mentioned or most important people.
        """
        if not self.people:
            return ""
        
        # Sort by most recently mentioned
        sorted_people = sorted(
            self.people.values(),
            key=lambda p: p.last_mentioned,
            reverse=True
        )[:max_people]
        
        lines = ["User's important relationships:"]
        for p in sorted_people:
            rel_desc = {
                "family": "family member",
                "romantic": "partner",
                "friend": "friend",
                "colleague": "colleague",
            }.get(p.relationship, "acquaintance")
            
            aliases = f" (also known as {', '.join(p.aliases)})" if p.aliases else ""
            lines.append(f"- {p.name}{aliases}: {rel_desc}")
        
        return "\n".join(lines)
