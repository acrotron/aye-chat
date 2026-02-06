#!/usr/bin/env python3
"""Manual test for the streaming UI with fixed viewport.

Run: python test_streaming_manual.py
"""

import time
import sys
import os

sys.path.insert(0, 'src')

from rich.console import Console
from aye.presenter.streaming_ui import StreamingResponseDisplay


def test_basic_viewport():
    """Basic viewport test - content stays in fixed area."""
    console = Console()
    
    content = '''# Streaming Test

This is a test of the **streaming UI** with a fixed viewport.

## Features

- Fixed viewport height
- Shows last N lines during streaming
- Full content after completion

## Code Example

```python
def hello():
    print("Hello, World!")
    return True
```

## More Content

- Item 1
- Item 2  
- Item 3
- Item 4
- Item 5

> This is a blockquote that appears near the end.

Done!
'''

    print("=" * 60)
    print("TEST: Fixed Viewport Streaming")
    print("Watch content stream in a fixed area (last 15 lines).")
    print("After completion, full content is displayed.")
    print("=" * 60)
    print()
    
    with StreamingResponseDisplay(console=console) as display:
        for i in range(len(content)):
            display.update(content[:i+1])
            time.sleep(0.015)
    
    print("Done! Full content is now visible above.")
    print()


def test_long_content():
    """Test with content that exceeds viewport."""
    console = Console()
    
    print("=" * 60)
    print("TEST: Long Content (exceeds viewport)")
    print("During streaming, you see a 'window' of the last 15 lines.")
    print("=" * 60)
    print()
    
    content = "# Long Content Test\n\n"
    content += "This document has many sections to test viewport scrolling.\n\n"
    
    with StreamingResponseDisplay(console=console) as display:
        display.update(content)
        time.sleep(0.2)
        
        for i in range(15):
            content += f"## Section {i+1}\n\n"
            content += f"This is the content for section {i+1}. " * 2 + "\n\n"
            content += f"```python\ndef func_{i+1}():\n    return {i+1}\n```\n\n"
            display.update(content)
            time.sleep(0.15)
    
    print("Done! Full long content visible above.")
    print()


def test_code_blocks():
    """Test code block rendering in viewport."""
    console = Console()
    
    content = '''Here's some Python code:

```python
import asyncio

async def main():
    print("Hello from async!")
    await asyncio.sleep(1)
    print("Done!")

asyncio.run(main())
```

And JavaScript:

```javascript
const fn = async () => {
    const response = await fetch('/api');
    return response.json();
};
```

And shell:

```bash
#!/bin/bash
echo "Hello World"
for i in {1..5}; do
    echo "Count: $i"
done
```

That's all the code!
'''

    print("=" * 60)
    print("TEST: Code Blocks in Viewport")
    print("=" * 60)
    print()
    
    with StreamingResponseDisplay(console=console) as display:
        for i in range(len(content)):
            display.update(content[:i+1])
            time.sleep(0.01)
    
    print("Done!")
    print()


def test_custom_viewport_height():
    """Test with custom viewport height."""
    console = Console()
    
    # Set custom viewport height for this test
    old_height = os.environ.get("AYE_STREAM_VIEWPORT_HEIGHT")
    os.environ["AYE_STREAM_VIEWPORT_HEIGHT"] = "8"  # Smaller viewport
    
    try:
        print("=" * 60)
        print("TEST: Custom Viewport Height (8 lines)")
        print("=" * 60)
        print()
        
        content = "# Small Viewport Test\n\n"
        for i in range(20):
            content += f"Line {i+1}: Content here...\n"
        
        with StreamingResponseDisplay(console=console) as display:
            for i in range(len(content)):
                display.update(content[:i+1])
                time.sleep(0.01)
        
        print("Done!")
        print()
    finally:
        if old_height is None:
            os.environ.pop("AYE_STREAM_VIEWPORT_HEIGHT", None)
        else:
            os.environ["AYE_STREAM_VIEWPORT_HEIGHT"] = old_height


def test_chunk_streaming():
    """Test realistic chunk-based streaming."""
    console = Console()
    
    print("=" * 60)
    print("TEST: Chunk-based Streaming (realistic API simulation)")
    print("=" * 60)
    print()
    
    chunks = [
        "# API Response\n\n",
        "Here is a ",
        "**bold** statement ",
        "and some ",
        "*italic* text.\n\n",
        "## Key Points\n\n",
        "- First point\n",
        "- Second point\n",
        "- Third point\n\n",
        "```python\n",
        "def example():\n",
        "    return 'hello'\n",
        "```\n\n",
        "That's all!",
    ]
    
    with StreamingResponseDisplay(console=console) as display:
        content = ""
        for chunk in chunks:
            content += chunk
            display.update(content)
            time.sleep(0.15)
    
    print("Done!")
    print()


def main():
    print()
    print("Streaming UI Test Suite - Fixed Viewport")
    print("=" * 60)
    print("This version uses a fixed viewport (default 15 lines).")
    print("During streaming, you see the last N lines.")
    print("After completion, full formatted content is shown.")
    print()
    
    tests = [
        ("1", "Basic Viewport", test_basic_viewport),
        ("2", "Long Content", test_long_content),
        ("3", "Code Blocks", test_code_blocks),
        ("4", "Custom Viewport Height (8 lines)", test_custom_viewport_height),
        ("5", "Chunk-based Streaming", test_chunk_streaming),
    ]
    
    print("Tests:")
    for num, name, _ in tests:
        print(f"  {num}. {name}")
    print("  a. Run all")
    print("  q. Quit")
    print()
    
    while True:
        choice = input("Select (1-5, a, q): ").strip().lower()
        
        if choice == 'q':
            break
        elif choice == 'a':
            for _, _, fn in tests:
                fn()
                time.sleep(0.5)
        elif choice in ['1', '2', '3', '4', '5']:
            tests[int(choice) - 1][2]()
        else:
            print("Invalid choice.")
        print()


if __name__ == "__main__":
    main()
