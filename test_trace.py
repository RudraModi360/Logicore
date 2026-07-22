"""Trace exactly what happens to multimodal content through the pipeline."""
import json
from logicore.agent.input_enricher import InputEnricher
from logicore.providers.utils import extract_content

# Step 1: InputEnricher enrichment
enricher = InputEnricher()
raw_input = '"C:\\Users\\rudra\\Desktop\\file_test.jpeg" what is in this image , describe it'
enriched = enricher.enrich(raw_input)
print("=== STEP 1: InputEnricher.enrich() ===")
print(f"Type: {type(enriched).__name__}")
print(f"Content: {json.dumps(enriched, indent=2)}")
print()

# Step 2: Sanitizer (what _sanitize_user_input does)
from logicore.security.input_sanitizer import InputSanitizer, InjectionAction
sanitizer = InputSanitizer(action=InjectionAction.WARN)
if isinstance(enriched, list):
    for part in enriched:
        if isinstance(part, dict):
            for key in ("text", "content"):
                val = part.get(key)
                if isinstance(val, str):
                    r = sanitizer.sanitize(val)
                    if not r.was_blocked:
                        part[key] = r.sanitized
sanitized_input = enriched
print("=== STEP 2: After sanitization ===")
print(f"Type: {type(sanitized_input).__name__}")
print(f"Content: {json.dumps(sanitized_input, indent=2)}")
print()

# Step 3: What session.add_message receives
msg = {"role": "user", "content": sanitized_input}
print("=== STEP 3: session.add_message receives ===")
print(f"Message: {json.dumps(msg, indent=2)}")
print()

# Step 4: What extract_content sees when gateway processes it
raw_content = msg.get("content", "")
text_content, images = extract_content(raw_content)
print("=== STEP 4: extract_content (gateway layer) ===")
print(f"Text: {text_content}")
print(f"Images count: {len(images)}")
for img in images:
    print(f"  - url={img.get('url')}, data_len={len(img.get('data', b'')) if img.get('data') else 0}")
print()

# Step 5: Now simulate what the session snapshot shows
# The snapshot shows content = [{"role": "system", ...}, {"role": "user", ...}]
# This is what inject_hint produces AFTER the user message is added
print("=== STEP 5: Session snapshot analysis ===")
# The snapshot shows the user message content is a list of role-keyed dicts
# This means the user message content was NOT the multimodal list
# Let's check: does inject_hint modify existing messages?
from logicore.runtime.context.message_pipeline import MessagePipeline

test_messages = []
test_messages.append(msg)  # Add user message first
print(f"Before inject: {len(test_messages)} messages")
print(f"User msg content type: {type(test_messages[0]['content']).__name__}")

# Now inject a hint (position=-1 means before last message)
MessagePipeline.inject_system_message(test_messages, "Test hint", position=-1)
print(f"After inject: {len(test_messages)} messages")
for i, m in enumerate(test_messages):
    print(f"  [{i}] role={m.get('role')}, content_type={type(m.get('content')).__name__}")
    if m.get('role') == 'user':
        content = m.get('content')
        if isinstance(content, list):
            for j, part in enumerate(content):
                print(f"      part[{j}]: {json.dumps(part, indent=8)[:200]}")
