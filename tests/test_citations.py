import os, random, sys
os.environ.update(MONGODB_URI="x", GROQ_API_KEY="x", GEMINI_API_KEY="x")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app.services.citations import CitationFilter, sanitize_citations, strip_citations

def S(t, n=5): return sanitize_citations(t, n)

def test_user_example():
    t = "come from a single file named `rag_retrieval_test_corpus.txt` (one document)【1†L1-L4】【2†L1-L4】."
    assert S(t) == ("come from a single file named `rag_retrieval_test_corpus.txt` (one document)[1][2].", [1, 2])

def test_styles():
    assert S("a [F ^1 L1-L4] b")[0] == "a [1] b"
    assert S("a [Source 2] b")[0] == "a [2] b"
    assert S("a [source: 3] b")[0] == "a [3] b"
    assert S("a [^4] b")[0] == "a [4] b"
    assert S("a 【3】 b")[0] == "a [3] b"
    assert S("a 【3†L5-L9】 b")[0] == "a [3] b"
    assert S("a [1, 2] b") == ("a [1][2] b", [1, 2])
    assert S("a [1-3] b") == ("a [1][2][3] b", [1, 2, 3])

def test_invalid_numbers_dropped():
    assert S("fact 【9†L1-L2】.") == ("fact.", [])
    assert S("fact [7].", 3) == ("fact.", [])
    assert S("x [1, 9] y", 3) == ("x [1] y", [1])

def test_not_citations_untouched():
    for t in ["[Table 1]", "[link text](https://a.com)", "[2024]", "[Q3 report]", "see [a] and [b]", "[Step 2]"]:
        assert S(t)[0] == t, t

def test_code_untouched():
    t = "use `arr[1]` here\n```py\nx = a[2]\ny = [1]\n```\nafter [1]"
    out, cited = S(t)
    assert out == t and cited == [1]

def test_unclosed():
    assert S("end 【1†L1")[0] == "end "
    assert S("open [ bracket")[0] == "open [ bracket"

def test_strip():
    assert strip_citations("Revenue rose [1][2]. Also [3] true.") == "Revenue rose. Also true."

def test_streaming_matches_oneshot_for_any_split():
    text = ("Intro `a[1]` text【1†L1-L4】【2†L1-L4】. Then [F ^3 L1-L2] and [Source 9] and [1, 2].\n"
            "```\nz[1]\n```\n[link](http://x.y) end [Table 1] 【4】 done [5]")
    want = S(text)
    rnd = random.Random(7)
    for _ in range(500):
        f, out, i = CitationFilter(5), [], 0
        while i < len(text):
            k = rnd.randint(1, 6); out.append(f.feed(text[i:i+k])); i += k
        out.append(f.flush())
        got = "".join(out)
        assert f.cited == want[1], (f.cited, want)
        assert got.replace(" .", ".") == want[0].replace(" .", "."), (got, want)

def test_streaming_holds_back_partial():
    f = CitationFilter(5)
    assert f.feed("see [1†L") == "see "
    assert f.feed("1-L4] ok") == "[1] ok"