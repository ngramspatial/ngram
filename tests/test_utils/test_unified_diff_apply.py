from ngram.utils.unified_diff_apply import apply_unified_diff


def test_apply_simple_hunk():
    orig = "a\nb\nc\n"
    patch = """--- a/x
+++ b/x
@@ -1,3 +1,3 @@
 a
-b
+x
 c
"""
    assert apply_unified_diff(orig, patch) == "a\nx\nc\n"


def test_apply_preserves_trailing_newline_when_original_had():
    orig = "only\n"
    patch = """@@ -1,1 +1,1 @@
-only
+ONLY
"""
    out = apply_unified_diff(orig, patch)
    assert out.endswith("\n")
