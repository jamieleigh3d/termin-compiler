"""AI Provider — calls LLM APIs for Compute execution.

Supports two modes:
- Level 1 (Provider is "llm"): Field-to-field completion with tool_use output
- Level 3 (Provider is "ai-agent"): Autonomous agent with ComputeContext tools

Built-in providers: Anthropic (Claude) and OpenAI (GPT).
Provider is selected via deploy config ai_provider section.
"""

import json
import logging
from typing import Any, Optional

logger = logging.getLogger("termin.ai_provider")


class AIProviderError(Exception):
    """Error from AI provider (API failure, missing config, etc.)."""
    pass


class AIProvider:
    """Manages LLM API calls for Compute execution."""

    def __init__(self, deploy_config: dict):
        self._config = deploy_config.get("ai_provider", {})
        self._service = self._config.get("service", "")
        self._model = self._config.get("model", "")
        self._api_key = self._config.get("api_key", "")
        self._client = None

    @property
    def is_configured(self) -> bool:
        return bool(self._service and self._api_key and "${" not in self._api_key)

    @property
    def service(self) -> str:
        return self._service

    @property
    def model(self) -> str:
        return self._model

    def startup(self):
        """Initialize the LLM client."""
        if not self.is_configured:
            if not self._service:
                logger.warning("AI provider not configured — no 'ai_provider' section in deploy config")
            elif not self._api_key:
                logger.warning(f"AI provider '{self._service}' has no API key — set the environment variable (e.g., ANTHROPIC_API_KEY)")
            elif "${" in self._api_key:
                # Show the variable name but not the value
                import re
                var_match = re.search(r'\$\{([^}]+)\}', self._api_key)
                var_name = var_match.group(1) if var_match else "unknown"
                logger.warning(f"AI provider API key contains unresolved variable ${{{var_name}}} — export it in your shell: export {var_name}=\"your-key\"")
            elif "\n" in self._api_key or "\r" in self._api_key:
                logger.error(f"AI provider API key contains a newline character — check your .bashrc or environment for a line break in the key")
            else:
                logger.warning("AI provider not configured — LLM Computes will be skipped")
            return

        if self._service == "anthropic":
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self._api_key)
                logger.info(f"Anthropic client initialized (model: {self._model})")
            except ImportError:
                logger.error("anthropic package not installed. Run: pip install anthropic")
        elif self._service == "openai":
            try:
                import openai
                self._client = openai.OpenAI(api_key=self._api_key)
                logger.info(f"OpenAI client initialized (model: {self._model})")
            except ImportError:
                logger.error("openai package not installed. Run: pip install openai")
        else:
            logger.error(f"Unknown AI provider service: {self._service}")

    async def complete(self, system_prompt: str, user_message: str,
                       output_tool: dict) -> dict:
        """Level 1: Single completion with forced tool_use output.

        Args:
            system_prompt: The Directive (system message).
            user_message: The Objective with input field values interpolated.
            output_tool: The auto-generated set_output tool schema.

        Returns:
            Dict with 'thinking' and output field values from the tool call.

        Raises:
            AIProviderError on failure.
        """
        if not self._client:
            raise AIProviderError("AI provider not initialized")

        if self._service == "anthropic":
            return await self._anthropic_complete(system_prompt, user_message, output_tool)
        elif self._service == "openai":
            return await self._openai_complete(system_prompt, user_message, output_tool)
        else:
            raise AIProviderError(f"Unknown service: {self._service}")

    async def agent_loop(self, system_prompt: str, user_message: str,
                         tools: list[dict],
                         execute_tool: Any) -> dict:
        """Level 3: Agent loop with tool calls.

        Args:
            system_prompt: Directive + tool descriptions.
            user_message: Objective + triggering record context.
            tools: List of tool schemas (ComputeContext tools + set_output).
            execute_tool: Async callable(tool_name, tool_input) -> result dict.

        Returns:
            The set_output tool call result (thinking + summary).

        Raises:
            AIProviderError on failure.
        """
        if not self._client:
            raise AIProviderError("AI provider not initialized")

        if self._service == "anthropic":
            return await self._anthropic_agent_loop(system_prompt, user_message, tools, execute_tool)
        elif self._service == "openai":
            return await self._openai_agent_loop(system_prompt, user_message, tools, execute_tool)
        else:
            raise AIProviderError(f"Unknown service: {self._service}")

    # ── Anthropic implementation ──

    async def _anthropic_complete(self, system_prompt: str, user_message: str,
                                   output_tool: dict) -> dict:
        import anthropic
        try:
            response = self._client.messages.create(
                model=self._model or "claude-sonnet-4-6",
                max_tokens=4096,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
                tools=[output_tool],
                tool_choice={"type": "tool", "name": "set_output"},
            )
            # Extract tool call result
            for block in response.content:
                if block.type == "tool_use" and block.name == "set_output":
                    return block.input
            raise AIProviderError("LLM did not call set_output tool")
        except anthropic.APIError as e:
            raise AIProviderError(f"Anthropic API error: {e}")

    async def _anthropic_agent_loop(self, system_prompt: str, user_message: str,
                                     tools: list[dict],
                                     execute_tool) -> dict:
        import anthropic
        messages = [{"role": "user", "content": user_message}]
        max_turns = 20

        for turn in range(max_turns):
            try:
                response = self._client.messages.create(
                    model=self._model or "claude-sonnet-4-6",
                    max_tokens=4096,
                    system=system_prompt,
                    messages=messages,
                    tools=tools,
                )
            except anthropic.APIError as e:
                raise AIProviderError(f"Anthropic API error on turn {turn}: {e}")

            # Check if the response has tool calls
            tool_calls = [b for b in response.content if b.type == "tool_use"]
            text_blocks = [b.text for b in response.content if b.type == "text"]

            if text_blocks:
                print(f"[Termin]   Turn {turn} thinking: {text_blocks[0][:100]}...")

            if not tool_calls:
                # Agent finished without calling set_output — extract text
                return {"thinking": " ".join(text_blocks), "summary": "Agent completed without set_output"}

            print(f"[Termin]   Turn {turn}: {len(tool_calls)} tool call(s): {', '.join(tc.name for tc in tool_calls)}")

            # Process tool calls
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []

            for tool_call in tool_calls:
                if tool_call.name == "set_output":
                    # Agent signals completion
                    print(f"[Termin]   Agent called set_output — completing")
                    return tool_call.input

                # Execute the tool via ComputeContext
                try:
                    result = await execute_tool(tool_call.name, tool_call.input)
                    result_summary = json.dumps(result)[:200] if isinstance(result, (dict, list)) else str(result)[:200]
                    print(f"[Termin]     {tool_call.name}() -> {result_summary}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_call.id,
                        "content": json.dumps(result) if isinstance(result, (dict, list)) else str(result),
                    })
                except Exception as e:
                    print(f"[Termin]     {tool_call.name}() ERROR: {e}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_call.id,
                        "content": f"Error: {e}",
                        "is_error": True,
                    })

            messages.append({"role": "user", "content": tool_results})

            if response.stop_reason == "end_turn":
                return {"thinking": "Agent ended turn", "summary": "Completed"}

        raise AIProviderError(f"Agent exceeded maximum turns ({max_turns})")

    # ── OpenAI implementation ──

    async def _openai_complete(self, system_prompt: str, user_message: str,
                                output_tool: dict) -> dict:
        import openai
        # Convert Anthropic-style tool to OpenAI format
        oai_tool = {
            "type": "function",
            "function": {
                "name": output_tool["name"],
                "description": output_tool.get("description", ""),
                "parameters": output_tool["input_schema"],
            }
        }
        try:
            response = self._client.chat.completions.create(
                model=self._model or "gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                tools=[oai_tool],
                tool_choice={"type": "function", "function": {"name": "set_output"}},
            )
            # Extract tool call
            choice = response.choices[0]
            if choice.message.tool_calls:
                tc = choice.message.tool_calls[0]
                return json.loads(tc.function.arguments)
            raise AIProviderError("LLM did not call set_output tool")
        except openai.APIError as e:
            raise AIProviderError(f"OpenAI API error: {e}")

    async def _openai_agent_loop(self, system_prompt: str, user_message: str,
                                  tools: list[dict],
                                  execute_tool) -> dict:
        import openai
        # Convert tools to OpenAI format
        oai_tools = []
        for t in tools:
            oai_tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t["input_schema"],
                }
            })

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        max_turns = 20

        for turn in range(max_turns):
            try:
                response = self._client.chat.completions.create(
                    model=self._model or "gpt-4o",
                    messages=messages,
                    tools=oai_tools,
                )
            except openai.APIError as e:
                raise AIProviderError(f"OpenAI API error on turn {turn}: {e}")

            choice = response.choices[0]

            if not choice.message.tool_calls:
                # No tool calls — agent finished
                return {"thinking": choice.message.content or "", "summary": "Completed"}

            messages.append(choice.message)

            for tc in choice.message.tool_calls:
                if tc.function.name == "set_output":
                    return json.loads(tc.function.arguments)

                try:
                    result = await execute_tool(tc.function.name, json.loads(tc.function.arguments))
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result) if isinstance(result, (dict, list)) else str(result),
                    })
                except Exception as e:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"Error: {e}",
                    })

            if choice.finish_reason == "stop":
                return {"thinking": "Agent stopped", "summary": "Completed"}

        raise AIProviderError(f"Agent exceeded maximum turns ({max_turns})")


def build_output_tool(output_fields: list[tuple[str, str]], content_lookup: dict) -> dict:
    """Build the set_output tool schema from Compute output field declarations.

    Args:
        output_fields: List of (content_ref, field_name) from IR.
        content_lookup: Dict of snake_name -> content schema dict.

    Returns:
        Anthropic-format tool schema dict.
    """
    # Fix 009.3: Don't include 'thinking' unconditionally.
    # It will only appear if the compute's output schema declares a 'thinking' field.
    properties = {}
    required = []

    for content_ref, field_name in output_fields:
        # Resolve content schema to get field type and enum constraints
        # content_ref is the singular (e.g., "completion"), need to find the content
        schema = None
        for name, s in content_lookup.items():
            singular = s.get("singular", "")
            if name == content_ref or singular == content_ref:
                schema = s
                break
        if not schema:
            # Fallback: just use string type
            properties[field_name] = {"type": "string", "description": f"Field: {content_ref}.{field_name}"}
            required.append(field_name)
            continue

        # Find the field definition
        field_def = None
        for f in schema.get("fields", []):
            fname = f.get("name", "")
            if fname == field_name:
                field_def = f
                break

        if field_def:
            prop = {"description": f"Field: {content_ref}.{field_name}"}
            enum_vals = field_def.get("enum_values", [])
            if enum_vals:
                prop["type"] = "string"
                prop["enum"] = list(enum_vals)
            elif field_def.get("column_type") in ("INTEGER", "REAL"):
                prop["type"] = "number"
            elif field_def.get("column_type") == "BOOLEAN":
                prop["type"] = "boolean"
            else:
                prop["type"] = "string"
            properties[field_name] = prop
            required.append(field_name)
        else:
            properties[field_name] = {"type": "string", "description": f"Field: {content_ref}.{field_name}"}
            required.append(field_name)

    return {
        "name": "set_output",
        "description": "Set the output fields for this computation. Always call this tool to provide your response.",
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        }
    }


def build_agent_tools(accesses: list[str], content_lookup: dict) -> list[dict]:
    """Build ComputeContext tool schemas for an agent.

    Args:
        accesses: List of content type snake_names the agent can touch.
        content_lookup: Dict of snake_name -> content schema dict.

    Returns:
        List of Anthropic-format tool schemas.
    """
    tools = [
        {
            "name": "content_query",
            "description": "Query records from a content table. Returns a list of records.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "content_name": {
                        "type": "string",
                        "description": f"Content type to query. Allowed: {', '.join(accesses)}",
                        "enum": accesses,
                    },
                    "filters": {
                        "type": "object",
                        "description": "Optional key-value filters (field_name: value).",
                        "additionalProperties": True,
                    },
                },
                "required": ["content_name"],
            },
        },
        {
            "name": "content_create",
            "description": "Create a new record in a content table. Returns the created record with id.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "content_name": {
                        "type": "string",
                        "description": f"Content type to create in. Allowed: {', '.join(accesses)}",
                        "enum": accesses,
                    },
                    "data": {
                        "type": "object",
                        "description": "Field values for the new record.",
                        "additionalProperties": True,
                    },
                },
                "required": ["content_name", "data"],
            },
        },
        {
            "name": "content_update",
            "description": "Update an existing record by id.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "content_name": {
                        "type": "string",
                        "enum": accesses,
                    },
                    "record_id": {"type": "integer", "description": "The record id to update."},
                    "data": {
                        "type": "object",
                        "description": "Fields to update.",
                        "additionalProperties": True,
                    },
                },
                "required": ["content_name", "record_id", "data"],
            },
        },
        {
            "name": "state_transition",
            "description": "Transition a record's state to a new state.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "content_name": {"type": "string", "enum": accesses},
                    "record_id": {"type": "integer"},
                    "target_state": {"type": "string", "description": "The state to transition to."},
                },
                "required": ["content_name", "record_id", "target_state"],
            },
        },
    ]
    return tools
