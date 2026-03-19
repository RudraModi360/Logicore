import os
from typing import Optional, List, Dict, Any
from logicore.providers.base import LLMProvider

class UserProfileManager:
    """
    Manages the user's profile, including mood, tone, and key information.
    Persists data to a Markdown file.
    """
    def __init__(self, profile_path: str, provider: LLMProvider):
        self.profile_path = profile_path
        self.provider = provider
        self._ensure_profile_exists()
        self.update_count = 0
        self.OPTIMIZE_FREQUENCY = 5 # Optimize every 5 updates

    def _ensure_profile_exists(self):
        if not os.path.exists(self.profile_path):
            # Create default if it doesn't exist
            with open(self.profile_path, 'w', encoding='utf-8') as f:
                f.write("# User Profile\n\n## General Mood & Tone\n- Neutral\n\n## Key Information\n- No information yet.")

    def get_profile(self) -> str:
        """Reads the current profile from the file."""
        if os.path.exists(self.profile_path):
            try:
                with open(self.profile_path, 'r', encoding='utf-8') as f:
                    return f.read()
            except Exception as e:
                print(f"[UserProfileManager] Error reading profile: {e}")
        return ""

    async def process_conversation_fragment(self, messages: List[Dict[str, Any]]):
        """
        Analyzes a chunk of conversation history and updates the profile.
        Used for batch processing to reduce latency/overhead.
        """
        if not messages:
            return

        current_profile = self.get_profile()
        
        # Format the conversation chunk for the LLM
        conversation_text = ""
        for msg in messages:
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')
            if role in ['user', 'assistant']: # We mostly care about these
                conversation_text += f"{role.upper()}: {content}\n"

        system_prompt = (
            "You are an expert user profiler. Your task is to update the user's profile based on a conversation fragment.\n"
            "The profile is stored in a Markdown file (`user_profile.md`).\n"
            "It contains:\n"
            "1. **General Mood & Tone**: The user's general vibe.\n"
            "2. **Key Information**: Facts about the user (Name, College, Preferences, etc.).\n\n"
            "**Instructions**:\n"
            "- Read the `Current Profile` and the `Conversation Fragment`.\n"
            "- Extract ANY new factual information provided by the USER (e.g., 'My name is Rudra', 'I like python').\n"
            "- Update 'Key Information' with these facts.\n"
            "- Analyze the user's tone across this fragment and update 'General Mood & Tone' if it has evolved.\n"
            "- **CRITICAL**: Return the **ENTIRE** updated Markdown content.\n"
            "- **CRITICAL**: Preserve existing info. Only ADD or UPDATE based on new evidence.\n"
            "- If no relevant info is found, return the `Current Profile` exactly as is.\n"
            "- Do not output markdown code blocks, just the raw text.\n"
        )

        messages_payload = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Current Profile:\n{current_profile}\n\nConversation Fragment:\n{conversation_text}\n\nUpdate the profile:"}
        ]

        try:
            response = await self.provider.chat(messages_payload, tools=None)
            
            content = ""
            if isinstance(response, dict):
                content = response.get('content', '')
            else:
                content = getattr(response, 'content', '')

            if content.startswith("```"):
                content = content.replace("```markdown", "").replace("```", "").strip()

            if content and "##" in content:
                self._save_profile(content)
                self.update_count += 1
                
                if self.update_count >= self.OPTIMIZE_FREQUENCY:
                    await self.optimize_profile()
                    self.update_count = 0
                
        except Exception as e:
            print(f"[UserProfileManager] Error updating profile: {e}")

    async def optimize_profile(self):
        """
        Condenses and organizes the profile to manage context length.
        """
        current_profile = self.get_profile()
        if len(current_profile) < 1000: # Skip if small
            return

        system_prompt = (
            "You are a Data Archivist. Your goal is to optimize the User Profile Markdown to be concise and dense.\n"
            "Rules:\n"
            "1. Remove redundant information.\n"
            "2. Merge related bullet points.\n"
            "3. Keep ALL factual data (names, dates, preferences).\n"
            "4. Summarize the 'Mood & Tone' to be brief but accurate.\n"
            "5. Maintain the standard Markdown structure.\n"
            "6. Output ONLY the optimized Markdown.\n"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Current Profile:\n{current_profile}\n\nOptimize this profile:"}
        ]

        try:
            # print("[UserProfileManager] Optimizing profile...")
            response = await self.provider.chat(messages, tools=None)
            
            content = ""
            if isinstance(response, dict):
                content = response.get('content', '')
            else:
                content = getattr(response, 'content', '')

            if content.startswith("```"):
                content = content.replace("```markdown", "").replace("```", "").strip()

            if content and "##" in content:
                self._save_profile(content)
                # print("[UserProfileManager] Profile optimized.")
                
        except Exception as e:
            print(f"[UserProfileManager] Error optimizing profile: {e}")

    def _save_profile(self, content: str):
        """Writes the updated profile to the file."""
        try:
            with open(self.profile_path, 'w', encoding='utf-8') as f:
                f.write(content)
        except Exception as e:
            print(f"[UserProfileManager] Error saving profile: {e}")
