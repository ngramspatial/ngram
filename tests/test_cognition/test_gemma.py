from ngram.cognition import gemma


def test_parse_thought_channel_exact_tokens():
    text = f"{gemma.CHANNEL_THOUGHT}\nsecret line\n{gemma.CHANNEL_END}Hello user"
    p = gemma.parse_assistant_output(text)
    assert p.thinking == "secret line"
    assert p.visible_user_text == "Hello user"


def test_parse_mangled_tool_token_echo_not_in_visible():
    """Unclosed <|tool_response|> blocks used to be emitted as visible text in full."""
    raw = f"{gemma.TOOL_RESPONSE_START}{gemma.TOOL_RESPONSE_START}{gemma.TOOL_CALL_END}"
    p = gemma.parse_assistant_output(raw)
    assert p.visible_user_text == ""


def test_strip_leaked_control_tokens():
    s = f"hi {gemma.TOOL_RESPONSE_START}there{gemma.TOOL_CALL_END}bye"
    assert gemma.strip_leaked_control_tokens(s) == "hi therebye"


def test_strip_leaked_control_tokens_mangled_concat():
    raw = "<|tool_response><tool_call|><|tool_response><|tool_response>"
    assert gemma.strip_leaked_control_tokens(raw) == ""
    out = gemma.strip_leaked_control_tokens(f"ok {raw} cool")
    assert out.split() == ["ok", "cool"]


def test_strip_leaked_informal_end_turn_with_colon_multiline():
    """Ollama sometimes emits end_turn as pseudo-JSON text instead of a real tool call."""
    leaked = (
        'end_turn: {\n'
        'thought: "Give the user the link.",\n'
        'mood: "neutral"\n'
        "}\n?"
    )
    assert gemma.strip_leaked_control_tokens(leaked).strip() == "?"


def test_strip_leaked_informal_tool_call_without_colon_still_stripped():
    assert gemma.strip_leaked_control_tokens('say {message: "hi"} there') == "there"


def test_strip_leaked_mangled_thought_channel_lines():
    raw = "<thought>\n<channel|><thought>\n<channel|>"
    assert gemma.strip_leaked_control_tokens(raw) == ""


def test_strip_leaked_length_stall_nudge():
    t = "ok\n\n(your response was too long — use write_file for large content)"
    assert "write_file" not in gemma.strip_leaked_control_tokens(t).lower()
    assert gemma.strip_leaked_control_tokens(t).strip() == "ok"


def test_parse_tool_call_gemma_delimiters():
    inner = 'call:weather{location:<|"|>London<|"|>}'
    raw = f"{gemma.TOOL_CALL_START}{inner}{gemma.TOOL_CALL_END}"
    p = gemma.parse_assistant_output(raw + "Nice day.")
    assert len(p.tool_call_raws) == 1
    name, args = gemma.parse_tool_call_inner(p.tool_call_raws[0])
    assert name == "weather"
    assert args.get("location") == "London"


def test_strip_thinking_for_history_preserves_tool_call():
    thought = f"{gemma.CHANNEL_THOUGHT}\ninside\n{gemma.CHANNEL_END}"
    tc = f"{gemma.TOOL_CALL_START}call:x{{}}{gemma.TOOL_CALL_END}"
    text = thought + tc + "tail"
    stripped = gemma.strip_thinking_for_history(text)
    assert "inside" not in stripped
    assert "call:x" in stripped
    assert "tail" in stripped


def test_inject_thinking():
    s = "You are kind."
    out = gemma.inject_thinking_instruction(s, True)
    assert gemma.THINK in out


def test_empty_thought_fragment_contains_channel():
    frag = gemma.empty_thought_channel_fragment()
    assert gemma.CHANNEL_THOUGHT in frag
    assert gemma.CHANNEL_END in frag


def test_separate_plaintext_cot_then_reply():
    raw = (
        "Thinking Process:\n\n"
        "1. Analyze input.\n"
        "2. Draft reply.\n\n"
        "yeah, man. just vibing."
    )
    vis, think = gemma.separate_plaintext_chain_of_thought(raw)
    assert "yeah, man" in vis
    assert "Thinking Process" in think
    assert "Analyze input" in think


def test_separate_plaintext_cot_only_triggers_empty_visible():
    raw = "Thinking Process:\n\n1. foo\n2. bar"
    vis, think = gemma.separate_plaintext_chain_of_thought(raw)
    assert vis == ""
    assert think == raw


def test_separate_plaintext_im_refinining_stays_in_thinking_not_visible():
    raw = (
        "Thinking Process:\n\n"
        "I'm refining how to state identity clearly.\n\n"
        "I'm ngram — a persistent entity on Gemma. Nice to meet you properly."
    )
    vis, think = gemma.separate_plaintext_chain_of_thought(raw)
    assert "ngram" in vis
    assert "refining" not in vis
    assert "refining" in think


def test_visible_reply_looks_truncated_stub():
    assert gemma.visible_reply_looks_truncated_stub("I'm")
    assert gemma.visible_reply_looks_truncated_stub("I’m")
    assert gemma.visible_reply_looks_truncated_stub("I am")
    assert not gemma.visible_reply_looks_truncated_stub("I'm ngram, hi.")


def test_visible_reply_looks_abruptly_cut():
    cut = "Okay. Fine. The literal name tag they slapped on me? I"
    assert gemma.visible_reply_looks_abruptly_cut(cut)
    assert not gemma.visible_reply_looks_abruptly_cut(cut + "'m ngram.")


def test_join_continuation_fragment():
    assert gemma.join_continuation_fragment("tag? I", "'m ngram.") == "tag? I'm ngram."
    assert gemma.join_continuation_fragment("Hello", "there") == "Hello there"


def test_format_tool_declarations_block():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_web",
                "description": "Search",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        }
    ]
    block = gemma.format_tool_declarations_block(tools)
    assert gemma.TOOL_DECL_START in block
    assert "search_web" in block
