"""The productive picker must expose current, source-backed provider breadth."""

from skaldir import model_picker as mp
from skaldir import setup_flow as sf


def test_relevant_chat_providers_reachable_in_product_picker():
    expected = {
        "openai", "anthropic", "google-gemini", "xai", "deepseek", "moonshot",
        "alibaba-qwen", "zai", "xiaomi-mimo", "openrouter", "mistral", "minimax",
        "groq", "cerebras", "together", "fireworks", "deepinfra", "siliconflow",
        "nebius", "novita", "sambanova", "perplexity", "cohere", "ai21",
        "llamacpp", "vllm", "lmstudio", "ollama", "typesafe", "opencode-go",
    }
    available = {entry.id for entry in sf.provider_choices()}
    assert expected <= available, f"Missing providers: {sorted(expected - available)}"
    assert available == {row.value for row in mp.provider_rows()}
