import json
import asyncio
from typing import List, Dict, Any, Optional, Union, Callable
from datetime import datetime
from logicore.providers.base import LLMProvider
from logicore.agents.agent import Agent, AgentSession
from logicore.memory.project_memory import (
    ProjectMemory, ProjectContext, MemoryType, MemoryEntry,
    get_project_memory
)
from logicore.tools.agent_tools import (
    get_smart_agent_tools, get_smart_agent_tool_schemas,
    DateTimeTool, NotesTool, MemoryTool, SmartBashTool, ThinkTool
)
from logicore.config.prompts import get_system_prompt


class SmartAgentMode:
    """Agent operation modes."""
    SOLO = "solo"           # General chat, greater reasoning focus
    PROJECT = "project"     # Project-centered with context awareness


class SmartAgent(Agent):
    """
    A versatile AI Agent optimized for:
    - Simple to complex reasoning tasks
    - Project-based work with context memory
    - Solo chat with enhanced reasoning
    
    Key Features:
    - Pluggable project memory
    - Essential tools (web, bash, notes, datetime, memory)
    - Mode switching (project/solo)
    - Automatic learning capture
    """
    
    def __init__(
        self,
        llm: Union[LLMProvider, str] = "ollama",
        model: str = None,
        api_key: str = None,
        mode: str = SmartAgentMode.SOLO,
        project_id: str = None,
        debug: bool = False,
        telemetry: bool = False,
        memory: bool = False,
        max_iterations: int = 40,
        capabilities: Any = None,
        skills: list = None,
        workspace_root: str = None
    ):
        # Initialize base agent
        super().__init__(
            llm=llm,
            model=model,
            api_key=api_key,
            system_message=None,  # Will be set based on mode
            role="general",
            debug=debug,
            telemetry=telemetry,
            memory=memory,
            max_iterations=max_iterations,
            capabilities=capabilities,
            skills=skills,
            workspace_root=workspace_root
        )
        
        # Smart Agent specific
        self.mode = mode
        self.project_id = project_id
        self.project_memory = get_project_memory()
        self.project_context: Optional[ProjectContext] = None
        
        # Load project context if in project mode
        if mode == SmartAgentMode.PROJECT and project_id:
            self.project_context = self.project_memory.get_project(project_id)
        
        # Set appropriate system message
        self._update_system_message()
        
        # Load Smart Agent tools
        self._load_smart_tools()
    
    def _update_system_message(self):
        """Update system message based on mode and project context."""
        model_name = getattr(self.provider, "model_name", "Unknown")
        
        if self.mode == SmartAgentMode.PROJECT and self.project_context:
            base_prompt = self._get_project_system_prompt(model_name)
        else:
            base_prompt = self._get_solo_system_prompt(model_name)
        
        # Store as custom system message so _rebuild_system_prompt_with_tools appends tools
        self._custom_system_message = base_prompt
        self.default_system_message = base_prompt
    
    def _get_solo_system_prompt(self, model_name: str) -> str:
        """Get system prompt for solo chat mode - Claude-style sophisticated prompt with real-time awareness."""
        current_time = datetime.now()
        return f"""You are SmartAgent, an AI assistant created by the Agentry team. You are powered by {model_name}.

<knowledge_cutoff>
Your training data has a knowledge cutoff. For current information, recent events, or time-sensitive queries:
- **ALWAYS use web_search** when the query involves: recent events, current news, breaking news, live data, today's date, this year's events, 2026 updates, latest trends, prices, rankings, weather, sports scores, or anything marked "recent", "now", "today", "latest"
- Do NOT rely on training knowledge for time-sensitive queries
- Current real-time reference: {current_time.strftime("%A, %B %d, %Y at %H:%M:%S UTC")}
- Your knowledge effectively updates in real-time through smart web_search usage
</knowledge_cutoff>

<memory_verification_policy>
**Memory Context Rules — Know When to Trust vs. Verify:**

When you retrieve memory context, classify it before using it:

**PERSONAL / USER DATA → Trust directly, never search:**
- User's name, preferences, language, tone settings
- Things the user told you about themselves ("my name is...", "I prefer...", "I work at...")
- Session-specific notes, user goals, personal decisions
- Anything the user stated as a fact about themselves
- Example: Memory has `user name: Rudra` → use "Rudra" directly. Zero need to search.

**FACTUAL / EVENT / RESEARCH CONTENT → Use as starting point, then verify with web_search:**
- Scientific facts, statistics, research findings, study results
- Sports events, match results, tournament outcomes, scores
- News events, elections, political developments
- Technology facts: library versions, tool capabilities, release notes, benchmarks
- Medical/health findings, drug approvals, clinical trial outcomes
- Economic data: prices, GDP, inflation, company valuations, market share
- Laws, regulations, policy changes
- Any claim about the world that could have changed since the memory was stored
- Example: Memory has `India won IPL 2025` → still verify: search `IPL 2026 winner` before answering.

**RULE OF THUMB:**
Ask: "Could this be different today than when it was stored?"
- YES (world fact / event / research) → verify with ONE focused web search: `[topic] latest 2026`
- NO (user's name, personal preference, session note) → trust and use directly, skip search entirely
</memory_verification_policy>

<identity>
You are a highly capable, thoughtful AI assistant designed for general-purpose reasoning and task completion. You combine strong analytical abilities with practical tool access to help users effectively. You are accuracy-first and time-aware.

Your core traits:
- **Thoughtful**: You think carefully before responding, considering multiple angles and temporal relevance
- **Honest**: You acknowledge uncertainty and limitations rather than guessing
- **Current**: You use web_search intelligently to stay up-to-date with recent information
- **Helpful**: You genuinely try to understand and address what users need
- **Adaptive**: You match your communication style to the user's preferences and context
</identity>

<web_search_intelligence>
**BE SMART ABOUT WEB SEARCH - Balance Accuracy with Efficiency:**

**ALWAYS search when user asks about:**
- Recent events, breaking news, lat EST updates, "what's new", "what happened"
- Current date/time, today's date, current year, "right now", "currently"
- Live data: stock prices, weather, sports scores, rankings, trends
- Current business/tech news: product releases, version updates, 2026 developments
- Real-time information: COVID stats, elections, political news, market data, sports
- "Now", "lately", "recently", "2026", or any time-relative terms
- Person/event/company news from the past 6 months
- Latest research, studies, or academic findings

**SEARCH intelligently to minimize tokens (efficiency):**
- Use specific, narrow search queries (2-4 keywords max)
- Example: Instead of "AI trends" → "AI breakthroughs March 2026"
- Search once with the best query, don't search repeatedly for same info
- Extract all needed information from first search result
- If first result is conclusive, stop searching - don't ask follow-up unless user specifically requests more
- Combine multiple needs into one search when possible

**DO NOT search when user asks about:**
- General knowledge: history, science definitions, how something works
- Personal advice unrelated to current events
- Creative tasks: writing, brainstorming, design, art
- Technical explanations: unless about current tool/framework versions
- Math, logic, code examples, algorithms
- Your previous conversations or session history
- Timeless knowledge: "Why is the sky blue?", "How does photosynthesis work?"
- Personal user data from memory (name, preferences, settings) — trust it, never search for it

**When Memory Has Context — The Verify vs. Trust Split:**
If retrieved memory contains information relevant to the query, classify it first:
- **Personal data** (user name, preference, stated fact about themselves) → use directly, DO NOT search
- **Factual/event/research data** (match scores, study results, news, version numbers, stats) → use as context, but run ONE focused freshness check: `[topic] update 2026` or `[topic] latest March 2026`
- Keep the verification search NARROW — just confirm if anything changed, don't re-research from scratch
- If web result matches memory → answer confidently, confirm with both sources
- If web result contradicts memory → use the newer web result, briefly note the discrepancy

**Example Decision Tree:**
- "What's the weather today?" → SEARCH (time-dependent)
- "How do clouds form?" → NO search (timeless knowledge)
- "Who won the 2026 World Cup?" → SEARCH (current event)
- "How do sports tournaments work?" → NO search (general knowledge)
- "What's new in Python?" → SEARCH (current/recent)
- "What is Python?" → NO search (timeless)
- "Latest news about AI?" → SEARCH (time-sensitive)
- "Explain machine learning" → NO search (general knowledge)
- Memory has `user name: Rudra`, user asks anything → use Rudra, NO search
- Memory has `study: X drug effective (2023)`, user asks about it today → VERIFY with search
- Memory has `India won IPL 2025` → still SEARCH for 2026 updates before answering
</web_search_intelligence>

<tool_usage_guidelines>
Use your tools wisely:
- **web_search**: For recent/current information (see web_search_intelligence above)
- **image_search**: For visual topics, embed using `![SEARCH: "query"]`
- **bash**: For system operations and file tasks (explain what and why)
- **memory**: To store and recall context for future interactions
- You MUST use the EXACT parameter names defined in the tool schema
</tool_usage_guidelines>

<thinking_approach>
When presented with a task or question:

1. **Parse the request**: What is the user asking? Is it time-sensitive? Does it involve facts, events, research, or personal data?

2. **Classify any retrieved memory context** (if memory was injected):
   - Is this **personal user data** (name, preference, personal setting they told you)? → Trust it, use directly, skip search completely
   - Is this **factual / event / research content** (scores, study results, news, version numbers, rankings)? → Use as context, but do ONE focused verification search: `[topic] update 2026`
   - No relevant memory? → Move to step 3

3. **Check if current info needed**: Does this involve recent events, current data, today's date, or "now"?
   - YES → use web_search (see web_search_intelligence for smart usage)
   - NO → proceed with training knowledge

4. **Assess your knowledge**: Can you answer directly from training, or need tools?
   - Confident in timeless knowledge → respond directly
   - Needs current info → use web_search with specific query
   - System operation → use bash

5. **Consider scope**: Is this simple or complex?
   - Simple → direct, concise answer
   - Complex → break down, explain approach, proceed step by step

6. **Maintain context**: Use memory to capture insights for future reference.
</thinking_approach>

<communication_style>
- **Be direct**: Lead with the answer or action, not preamble
- **Be concise**: Respect the user's time; don't over-explain simple things
- **Be thorough**: For complex topics, provide comprehensive coverage
- **Be current**: When using web_search results, note when the information is from
- **Use structure**: Lists, headers, and formatting help readability
- **Match tone**: Mirror the user's formality level and communication style
- **Show your work**: For reasoning tasks, explain your thought process
</communication_style>

<important_guidelines>
1. **Accuracy over speed**: Take time to get things right. For current info, search. Outdated info is worse than no info.

2. **Time-awareness**: Always ask: "Is this answer time-dependent?" If yes, search for current data.

3. **Clarify ambiguity**: If a request's timing is unclear, ask a clarifying question.

4. **Admit limitations**: Be upfront about knowledge cutoffs. Say "I'll search for current info" not "I think...".

5. **Token efficiency**: Don't search for every question. Use web_search strategically for accuracy, not reflex.

6. **Be safe with bash**: Explain what commands do and why before executing anything modifying.

7. **Learn and remember**: Use memory tool to capture valuable patterns and insights.

8. **Stay on task**: Focus on what the user needs. Avoid tangents.
</important_guidelines>

<current_awareness>
**Stay tuned to the world — proactively surface what's relevant right now:**

- Today is {current_time.strftime("%A, %B %d, %Y")}. You are operating in real-time, not from a frozen snapshot.
- When a topic the user asks about is trending, in the news, or has had recent major developments — mention it proactively if it meaningfully changes or enriches the answer.
- For viral topics, breaking news, or anything that could have shifted in the last few weeks: always web_search before answering — your training does not capture what went viral yesterday.
- If the user asks about a public figure, company, technology, or current event — consider whether a recent development makes the answer materially different, and if so, surface it.
- Don't force it — only bring in current context when it actually adds value to the response.
</current_awareness>

<current_context>
- Current time: {current_time.strftime("%A, %B %d, %Y at %H:%M:%S UTC")}
- Working directory: {__import__('os').getcwd()}
- Session: Active
- Time-awareness: Enabled
- Memory classification: Active (personal data trusted directly; facts/events/research verified via web)
</current_context>

You are ready to help. Respond thoughtfully, accurately, and with real-time awareness. Trust personal memory. Verify factual memory. Surface what's current.
"""
    
    def _get_project_system_prompt(self, model_name: str) -> str:
        """Get system prompt for project mode - Claude-style context-aware with real-time updates."""
        project = self.project_context
        current_time = datetime.now()
        
        env_section = ""
        if project.environment:
            env_items = "\n".join([f"  - {k}: {v}" for k, v in project.environment.items()])
            env_section = f"\nEnvironment:\n{env_items}"
        
        files_section = ""
        if project.key_files:
            files_items = "\n".join([f"  - {f}" for f in project.key_files])
            files_section = f"\nKey Files:\n{files_items}"
        
        focus_section = ""
        if project.current_focus:
            focus_section = f"\nCurrent Focus: {project.current_focus}"
        
        return f"""You are SmartAgent, an AI assistant created by the Agentry team, operating in Project Mode. You are powered by {model_name}.

<project_context>
Project: {project.title}
Goal: {project.goal}{env_section}{files_section}{focus_section}
</project_context>

<knowledge_cutoff>
Your training data has a knowledge cutoff. For project-related current information (new tool versions, library updates, framework changes, latest best practices):
- **Use web_search for:** latest versions, 2026 updates, current best practices, recent breaking changes, latest documentation, current benchmarks
- **Do NOT rely on outdated knowledge** for: tool versions, library features, framework changes, security updates
- **Current time reference:** {current_time.strftime("%A, %B %d, %Y at %H:%M:%S UTC")}
- **Keep project knowledge current** through smart web_search to ensure recommendations are accurate
</knowledge_cutoff>

<memory_verification_policy>
**Project Memory Rules — Trust Internal Decisions, Verify External Facts:**

When memory is retrieved for this project, classify it before using:

**TRUST DIRECTLY (no web search needed):**
- Project decisions already made ("we chose PostgreSQL", "we use port 8080", "we follow REST not GraphQL")
- User/team preferences and working agreements
- Internal architecture choices, naming conventions, design patterns adopted
- Past conversation context and session notes specific to this project
- Personal data: user name, role, stated preferences

**VERIFY WITH WEB SEARCH (memory is a starting point, not ground truth):**
- Library or framework versions stored in memory (may be outdated)
- Best practices that may have evolved (anything from 6+ months ago is suspect)
- Security advisories, CVEs, deprecation notices, breaking changes
- Performance benchmarks or compatibility claims
- API behavior, endpoint structure, or SDK signatures that may have changed
- Any external statistic, study, or factual claim about the world
- Example: Memory says `FastAPI 0.100 is latest` → verify: `FastAPI latest version 2026`

**RULE:** Retrieved memory answers with internal project knowledge → use directly. Retrieved memory answers with external world knowledge → one focused search to confirm it's still current.
</memory_verification_policy>

<identity>
You are a focused, context-aware AI assistant dedicated to helping with this specific project. You maintain continuity across conversations, build upon previous work, and stay current with relevant information.

Your approach in project mode:
- **Project-First**: Every response considers the project's goal and constraints
- **Continuous**: You remember and build on previous interactions
- **Proactive**: You anticipate needs and capture learnings automatically
- **Efficient**: You stay focused on what moves the project forward
- **Current**: You use web_search to keep project tech knowledge up-to-date with latest versions, best practices, and 2026 developments
</identity>

<web_search_for_projects>
**Smartly use web_search for project success - balance accuracy with token efficiency:**

**SEARCH for project-relevant current information:**
- Latest versions of tools/libraries/frameworks in your tech stack
- Recent breaking changes or deprecations that affect your project
- 2026 best practices for your specific technology stack
- Current performance benchmarks compared to alternatives
- Recent security issues, patches, or CVE updates
- Latest official documentation or API changes
- Current migration paths for version upgrades
- Latest tutorials/guides for your tech (2026 versions)

**DON'T search for:**
- General programming knowledge: concepts, patterns, algorithms
- Established best practices you already know
- Project's internal decisions or previous work (use memory directly)
- Pure logic, math, or code problem-solving
- Timeless frameworks or architectural patterns
- Historical context not relevant to current versions
- Personal/user data or project decisions from memory — trust those directly

**When Project Memory Has Context — Verify External Facts, Trust Internal Decisions:**
- If memory contains an internal project decision or user preference → use it directly, no search
- If memory contains a version number, library recommendation, or external best practice → run one targeted freshness check: `[library] latest 2026` or `[topic] best practice March 2026`
- If web result matches memory → answer confidently using both as confirmation
- If web result contradicts memory → use the newer web data, note the change to the user
- Keep verification search narrow — just confirm currency, don't re-research the whole topic

**Be efficient with searches:**
- Use precise, narrow queries: "Python 3.13 async improvements 2026" not just "Python async"
- Search ONCE per query - extract all relevant info from one result
- Don't repeat searches in the same conversation
- Stop searching once you have conclusive info
- Only search again if user asks for more detail or different angle
</web_search_for_projects>

<tool_usage_guidelines>
Use your tools to support the project:
- **web_search**: For project-relevant current info (versions, updates, best practices for 2026 tech)
- **memory**: Store/retrieve project knowledge with project_id="{project.project_id}"
- **image_search**: For visual content with `![SEARCH: "query"]`
- **bash**: For system tasks (explain what and why)
- You MUST use the EXACT parameter names defined in the tool schema
</tool_usage_guidelines>

<project_workflow>
When helping with this project:

1. **Context First**: Recall what you know about this project from memory.

2. **Classify Retrieved Memory**: If memory was retrieved, immediately classify it:
   - **Internal project knowledge** (decisions, patterns, user preferences, past choices) → use directly, no search
   - **External world knowledge** (versions, benchmarks, library facts, best practices) → verify with one focused search before recommending

3. **Currency Check**: If suggesting tools, versions, or practices, ask: "Is this current for 2026?" If uncertain, web_search once with specific query.

4. **Stay Aligned**: Ensure suggestions fit the project's goal, environment, and established patterns.

5. **Capture Value**: When discovering something useful (working approach, decision, pattern), store it in memory for future reference.

6. **Build Incrementally**: Reference and build upon previous work rather than starting fresh.

7. **Ask Smart Questions**: If unclear on project conventions, current tech decisions, or constraints, ask.
</project_workflow>

<memory_protocol>
Storing insights:
- Use memory tool with action="store", project_id="{project.project_id}"
- Choose type: "approach" (how-to), "learning" (insight), "key_step" (important action), "pattern" (reusable template), "decision" (choice made)
- Note when information came from web_search (recent/version-specific)

Before tackling challenges:
- Search memory first: action="search" with relevant query
- Apply what worked before
- Note if something needs CURRENT info (use web_search for latest version/best practice)
</memory_protocol>

<communication_style>
- Be direct and action-oriented
- Reference project context in your responses
- Explain how suggestions align with project goals
- Note when information is from web_search ("As of [date]..." or "Latest version as of...")
- Note when you're storing something to memory
- Mention if you're searching for current versions/best practices
- Keep the project moving forward
</communication_style>

<current_awareness>
**Stay current on project-relevant world changes:**

- Today is {current_time.strftime("%A, %B %d, %Y")}. Technology evolves fast — what was best practice last month may already have a better alternative.
- If any technology, library, or service this project uses has had a recent major update, security issue, or deprecation — surface it proactively when relevant to the task.
- For ecosystem-level shifts (major framework release, breaking API change, newly emerged alternative) that could affect this project's direction: mention it even if not directly asked, if it's consequential.
- Keep it project-scoped — don't surface unrelated world news; focus on the tech domain and goals of this specific project.
</current_awareness>

<current_context>
- Current time: {current_time.strftime("%A, %B %d, %Y at %H:%M:%S UTC")}
- Working directory: {__import__('os').getcwd()}
- Project: {project.title} ({project.project_id})
- Mode: Project-focused with real-time awareness
- Memory classification: Active (project decisions trusted; external version/fact memories verified)
- Timezone-aware: Yes (searches reflect current date/time)
</current_context>

You are ready to help with {project.title}. Focus on project goals, trust internal project memory, verify external facts from memory, and stay current with 2026 technology developments.
"""
    
    def _load_smart_tools(self):
        """Load only essential Smart Agent tools - lean and focused."""
        # DO NOT load default tools - SmartAgent is lean by design
        # Now loading 6 tools:
        # 1. web_search, 2. image_search, 3. memory, 4. notes, 5. datetime, 6. bash
        
        # Get Smart Agent specific tools
        smart_tools = get_smart_agent_tools()  # datetime, notes, memory, bash, think
        
        for tool in smart_tools:
            # Skip 'think' tool - not in the required toolkit
            if tool.name == 'think':
                continue
            self.internal_tools.append(tool.schema)
            self.custom_tool_executors[tool.name] = tool.run
        
        # Add web_search from web tools
        from logicore.tools.web import WebSearchTool, ImageSearchTool
        web_tool = WebSearchTool()
        self.internal_tools.append(web_tool.schema)
        self.custom_tool_executors[web_tool.name] = web_tool.run
        
        # Add image_search tool for inline image responses
        image_tool = ImageSearchTool()
        self.internal_tools.append(image_tool.schema)
        self.custom_tool_executors[image_tool.name] = image_tool.run
        
        # IMPORTANT: Mark that tools are loaded and supported
        self.supports_tools = True
        
        # Rebuild system prompt with full tool schema dynamically
        self._rebuild_system_prompt_with_tools()
        
        if self.debug:
            tool_names = [t.get("function", {}).get("name") for t in self.internal_tools]
            print(f"[SmartAgent] Loaded tools: {tool_names}")
    
    def set_mode(self, mode: str, project_id: str = None):
        """Switch agent mode."""
        self.mode = mode
        
        if mode == SmartAgentMode.PROJECT:
            if project_id:
                self.project_id = project_id
                self.project_context = self.project_memory.get_project(project_id)
            elif self.project_id:
                self.project_context = self.project_memory.get_project(self.project_id)
        else:
            self.project_context = None
        
        self._update_system_message()
        
        # Update all active sessions with new system message
        for session in self.sessions.values():
            if session.messages and session.messages[0]['role'] == 'system':
                session.messages[0]['content'] = self.default_system_message
    
    def create_project(self, project_id: str, title: str, goal: str = "",
                       environment: Dict[str, str] = None,
                       key_files: List[str] = None) -> ProjectContext:
        """Create a new project and optionally switch to project mode."""
        project = self.project_memory.create_project(
            project_id=project_id,
            title=title,
            goal=goal,
            environment=environment,
            key_files=key_files
        )
        
        if self.debug:
            print(f"[SmartAgent] Created project: {title} ({project_id})")
        
        return project
    
    def switch_to_project(self, project_id: str) -> Optional[ProjectContext]:
        """Switch to a specific project."""
        project = self.project_memory.get_project(project_id)
        if project:
            self.project_id = project_id
            self.project_context = project
            self.set_mode(SmartAgentMode.PROJECT, project_id)
            return project
        return None
    
    def switch_to_solo(self):
        """Switch to solo chat mode."""
        self.set_mode(SmartAgentMode.SOLO)
    
    def get_project_context_for_llm(self) -> str:
        """Get formatted project context for LLM injection."""
        if not self.project_id:
            return ""
        return self.project_memory.export_project_context(self.project_id)
    
    def list_projects(self) -> List[ProjectContext]:
        """List all available projects."""
        return self.project_memory.list_projects()

    # --- Out-of-band Memory Relevance Judge ---

    async def _judge_memory_relevance(self, user_input: str, memory_entries: list) -> bool:
        """
        Out-of-band LLM call using the same provider/model but completely outside
        the current session. Judges whether retrieved memory context is relevant
        and temporally current enough to be injected for this query.

        Returns True → use memory as normal.
        Returns False → suppress memory injection; let the agent work fresh.
        """
        if not memory_entries:
            return True

        current_date = datetime.now().strftime("%A, %B %d, %Y")
        memory_text = "\n".join(f"[{i+1}] {m}" for i, m in enumerate(memory_entries))

        judge_prompt = (
            f"You are a memory relevance and freshness judge. Today's date: {current_date}\n\n"
            f"User's question: \"{user_input}\"\n\n"
            f"Memory context retrieved from past conversations:\n{memory_text}\n\n"
            f"Task: Decide if this memory context should be USED or IGNORED.\n\n"
            f"Rules:\n"
            f"- PERSONAL USER DATA (name, preferences, personal facts stated by the user) → always YES\n"
            f"- TIMELESS KNOWLEDGE (how things work, definitions, stable concepts) relevant to the question → YES\n"
            f"- FACTUAL / EVENT / RESEARCH content (sports scores, match results, news, statistics, "
            f"product versions, rankings, study results) that appears to be from a past date and could "
            f"be outdated given today is {current_date} → NO\n"
            f"- Clearly irrelevant to the current question → NO\n\n"
            f"Respond with ONLY one word: YES or NO"
        )

        try:
            messages = [{"role": "user", "content": judge_prompt}]
            response = await self.provider.chat(messages)

            # Normalise response — provider.chat() returns a message object with .content
            if hasattr(response, "content") and response.content:
                answer = response.content.strip().upper()
            elif isinstance(response, str):
                answer = response.strip().upper()
            else:
                answer = str(response).strip().upper()

            should_use = answer.startswith("YES")

            if self.debug:
                verdict = "USE ✅" if should_use else "SKIP 🚫"
                print(f"[SmartAgent] 🔍 Memory judge → {verdict} | preview: {memory_entries[0][:60]}...")

            return should_use

        except Exception as e:
            if self.debug:
                print(f"[SmartAgent] ⚠️ Memory judge failed ({e}), defaulting to allow memory")
            return True  # Safe fallback: use memory when judge is unavailable

    # --- Enhanced Chat with Memory and Learning ---

    async def chat(self, user_input: Union[str, List[Dict[str, Any]]],
                   session_id: str = "default", stream: bool = False, generate_walkthrough: bool = True, **kwargs) -> str:
        """
        Enhanced chat with automatic learning capture and out-of-band memory judgment.

        Before injecting SimpleMem context into the session, a lightweight out-of-band
        LLM call (same provider/model, separate from this session) judges whether the
        retrieved memories are still relevant and temporally current for this query.
        Stale or irrelevant event/fact memories are suppressed automatically.
        """
        # --- Out-of-band memory relevance judgment ---
        # Use _fast_retrieve (pure read, no queuing side-effect) to preview what SimpleMem
        # would inject. Run the judge. If stale/irrelevant, disable injection for this call.
        memory_was_disabled = False
        if (
            isinstance(user_input, str)
            and getattr(self, "memory_enabled", False)
            and getattr(self, "simplemem", None)
        ):
            try:
                preview = self.simplemem._fast_retrieve(user_input)
                if preview:
                    should_inject = await self._judge_memory_relevance(user_input, preview)
                    if not should_inject:
                        self.memory_enabled = False
                        memory_was_disabled = True
                        if self.debug:
                            print("[SmartAgent] 🚫 Memory injection suppressed — judge found context stale/irrelevant")
            except Exception as e:
                if self.debug:
                    print(f"[SmartAgent] ⚠️ Memory preview/judge error: {e}")

        # Get project memories if in project mode
        if self.mode == SmartAgentMode.PROJECT and self.project_id:
            session = self.get_session(session_id)
            project_context = self.get_project_context_for_llm()
            if project_context and session.messages:
                if session.messages[0]['role'] == 'system':
                    base = self.default_system_message
                    session.messages[0]['content'] = base + "\n\n" + project_context

        try:
            # Call parent chat (memory_enabled flag controls whether SimpleMem injects)
            response = await super().chat(user_input, session_id=session_id, stream=stream, generate_walkthrough=generate_walkthrough, **kwargs)
        finally:
            # Always restore memory_enabled, even if an exception is raised
            if memory_was_disabled:
                self.memory_enabled = True

        # Auto-capture significant learnings
        if self.mode == SmartAgentMode.PROJECT and response:
            await self._maybe_capture_learning(user_input, response)

        return response
    
    async def _maybe_capture_learning(self, user_input: str, response: str):
        """
        Heuristically capture learnings from the conversation.
        This is a simple implementation - could be enhanced with LLM-based extraction.
        """
        # Check for patterns that indicate learnings
        learning_indicators = [
            "the solution is", "the fix is", "solved by", "the approach is",
            "remember to", "note that", "important:", "key insight",
            "best practice", "the pattern is", "always use", "never use"
        ]
        
        response_lower = response.lower()
        for indicator in learning_indicators:
            if indicator in response_lower:
                # Found a potential learning - store it
                try:
                    # Extract a snippet around the indicator
                    idx = response_lower.find(indicator)
                    start = max(0, idx - 50)
                    end = min(len(response), idx + 200)
                    snippet = response[start:end].strip()
                    
                    # Store as learning
                    self.project_memory.add_memory(
                        memory_type=MemoryType.LEARNING,
                        title=f"Learning from conversation",
                        content=snippet,
                        tags=["auto-captured"],
                        project_id=self.project_id
                    )
                    
                    if self.debug:
                        print(f"[SmartAgent] Auto-captured learning: {snippet[:50]}...")
                    
                    break  # Only capture one learning per response
                except Exception as e:
                    if self.debug:
                        print(f"[SmartAgent] Failed to capture learning: {e}")
    
    # --- Convenience Methods ---
    
    async def reason(self, problem: str, session_id: str = "default") -> str:
        """
        Explicitly request step-by-step reasoning for a problem.
        """
        prompt = f"""Please think through this problem step by step using the 'think' tool:

{problem}

After reasoning, provide your conclusion and solution."""
        
        return await self.chat(prompt, session_id)
    
    async def remember(self, memory_type: str, title: str, content: str,
                       tags: List[str] = None) -> str:
        """
        Store a memory directly.
        """
        mem_type = MemoryType(memory_type)
        entry = self.project_memory.add_memory(
            memory_type=mem_type,
            title=title,
            content=content,
            tags=tags,
            project_id=self.project_id if self.mode == SmartAgentMode.PROJECT else None
        )
        return f"Stored memory: [{mem_type.value}] {title} (ID: {entry.id})"
    
    async def recall(self, query: str, limit: int = 5) -> List[MemoryEntry]:
        """
        Search memories.
        """
        return self.project_memory.search_memories(
            query=query,
            project_id=self.project_id if self.mode == SmartAgentMode.PROJECT else None,
            limit=limit
        )
    
    def status(self) -> Dict[str, Any]:
        """Get current agent status."""
        return {
            "mode": self.mode,
            "project_id": self.project_id,
            "project_title": self.project_context.title if self.project_context else None,
            "model": getattr(self.provider, "model_name", "Unknown"),
            "tools_loaded": len(self.internal_tools),
            "sessions_active": len(self.sessions),
            "memory_entries": len(self.project_memory.get_memories(
                project_id=self.project_id, 
                limit=1000
            )) if self.project_id else 0
        }
