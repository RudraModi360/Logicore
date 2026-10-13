import time
from openai import OpenAI

c = OpenAI(
    api_key="oc_sk_4c01c67f0b88_tcNDptmcoTcfpjkEouhfrzwMZa-Vq5Z9",
    base_url="https://opencode.ai/zen/v1",
    timeout=90,
)

tools = [
    {
        "type": "function",
        "function": {
            "name": f"tool_{i}",
            "description": f"tool number {i} does thing {i} " + ("word " * 40),
            "parameters": {
                "type": "object",
                "properties": {"q": {"type": "string", "description": "query"}},
                "required": ["q"],
            },
        },
    }
    for i in range(40)
]

filler = "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor. "


def build(n_tokens, emoji=True):
    body = (filler * (n_tokens // 16))
    if emoji:
        body = "\U0001F4CA search result \U0001F4C4 ok\n" * 50 + body
    return [
        {"role": "system", "content": "you are a research agent. " + body[: n_tokens * 3 // 2]},
        {"role": "user", "content": "find contacts " + ("\U0001F4CA" if emoji else "")},
    ]


def run(label, messages, max_tokens, stream):
    t = time.time()
    try:
        if stream:
            r = c.chat.completions.create(
                model="space-bunny-free",
                messages=messages,
                max_tokens=max_tokens,
                tools=tools,
                stream=True,
            )
            n = 0
            for _ in r:
                n += 1
            print("%-38s OK   %.1fs chunks=%d" % (label, time.time() - t, n))
        else:
            r = c.chat.completions.create(
                model="space-bunny-free",
                messages=messages,
                max_tokens=max_tokens,
                tools=tools,
            )
            print("%-38s OK   %.1fs prompt=%s completion=%s" % (label, time.time() - t, r.usage.prompt_tokens, r.usage.completion_tokens))
    except Exception as e:
        print("%-38s ERR  %.1fs %s" % (label, time.time() - t, str(e)[:150].replace("\n", " ")))


small = build(2000)
big = build(14000)
run("small stream mt=512", small, 512, True)
run("big(14k) stream mt=512", big, 512, True)
run("big(14k) plain mt=512", big, 512, False)
run("big(14k) stream mt=16", big, 16, True)
run("big(14k) plain mt=16", big, 16, False)
big_noemoji = build(14000, emoji=False)
run("big(14k) no-emoji stream mt=512", big_noemoji, 512, True)
run("again big stream mt=512", big, 512, True)
