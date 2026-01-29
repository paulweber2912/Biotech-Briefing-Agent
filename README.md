# Biotech-Briefing-Agent

## Prompt

Edit `prompts/daily_prompt.md` to change the briefing behavior without changing code.
The placeholder `{{today}}` is replaced automatically.

Optional env vars:
- `PROMPT_PATH` (default: prompts/daily_prompt.md)
- `ANTHROPIC_MODEL` (default: claude-3-5-sonnet-latest)
- `MAX_TOKENS` (default: 2200)
- `TEMPERATURE` (default: 0.2)
