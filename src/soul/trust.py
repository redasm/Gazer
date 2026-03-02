from pydantic import BaseModel, Field
from typing import Dict, Optional
import json
import logging
import os
import time

logger = logging.getLogger("GazerTrust")

class TrustLevel:
    """Trust level definitions."""
    PRIMARY = 100    # Primary user (owner)
    KNOWN = 50      # Known acquaintance
    STRANGER = 0    # Stranger

class Identity(BaseModel):
    """Individual identity definition."""
    name: str = "Unknown"
    trust_score: float = 0.0
    first_seen: float = Field(default_factory=time.time)
    last_seen: float = Field(default_factory=time.time)
    interaction_count: int = 0

class TrustSystem:
    """Identity recognition and social relationship modelling.

    Decides how to treat the person Gazer is interacting with.
    """
    def __init__(self, persist_path: Optional[str] = None):
        if persist_path is None:
            from runtime.config_manager import config as _cfg
            base_dir = str(_cfg.get("memory.context_backend.data_dir", "data/openviking") or "data/openviking")
            persist_path = os.path.join(base_dir, "trust.json")
        self.persist_path = persist_path
        self.identities: Dict[str, Identity] = {}
        self._load()

    def observe(self, identity_name: str, is_primary: bool = False):
        """Record an observation of an identity."""
        if identity_name not in self.identities:
            initial_trust = TrustLevel.PRIMARY if is_primary else TrustLevel.STRANGER
            self.identities[identity_name] = Identity(name=identity_name, trust_score=initial_trust)
        
        target = self.identities[identity_name]
        target.last_seen = time.time()
        target.interaction_count += 1
        
        # Gradually increase trust based on interaction frequency
        if not is_primary and target.trust_score < TrustLevel.KNOWN:
            target.trust_score += 0.5 
        
        self._save()

    def get_relationship_prompt(self, identity_name: str) -> str:
        """Generate a conversation guidance prompt based on trust level."""
        if identity_name not in self.identities:
            return "Be polite but reserved with this person -- it is your first encounter."

        target = self.identities[identity_name]
        if target.trust_score >= TrustLevel.PRIMARY:
            return "This is your primary user -- show high loyalty and warmth."
        elif target.trust_score >= TrustLevel.KNOWN:
            return "This is an acquaintance -- engage in natural, friendly conversation."
        else:
            return "This person is still a stranger -- maintain basic courtesy."

    def _load(self):
        """Load trust data from persistent storage."""
        if not os.path.exists(self.persist_path):
            return
        try:
            with open(self.persist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for name, vals in data.items():
                self.identities[name] = Identity(**vals)
            logger.info(f"Loaded {len(self.identities)} identities from {self.persist_path}")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load trust data: {e}")

    def _save(self):
        """Persist trust data to JSON file."""
        try:
            os.makedirs(os.path.dirname(self.persist_path), exist_ok=True)
            with open(self.persist_path, "w", encoding="utf-8") as f:
                json.dump(
                    {k: v.model_dump() for k, v in self.identities.items()},
                    f, ensure_ascii=False, indent=2
                )
        except OSError as e:
            logger.error(f"Failed to save trust data: {e}")
