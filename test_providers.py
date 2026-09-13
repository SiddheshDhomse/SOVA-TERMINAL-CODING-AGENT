"""Manual script to test both Groq and Nvidia providers with simple messages."""
import os
from dotenv import load_dotenv

from agent.loop import run_agent


def main():
    load_dotenv()
    test_messages = ["hey", "hello"]

    # Test Groq
    print("\n" + "=" * 60)
    print("TESTING GROQ PROVIDER")
    print("=" * 60)
    os.environ["SOVA_PROVIDER"] = "groq"

    for msg in test_messages:
        print(f"\n-> Testing with message: '{msg}'")
        try:
            result = run_agent(
                os.getcwd(),
                msg,
                on_event=lambda e: print(f"  Event: {e['type']}"),
            )
            print("[PASS] Groq response received")
        except Exception as e:
            print(f"[FAIL] Groq error: {e}")

    # Test Nvidia
    print("\n" + "=" * 60)
    print("TESTING NVIDIA PROVIDER")
    print("=" * 60)
    os.environ["SOVA_PROVIDER"] = "nvidia"

    for msg in test_messages:
        print(f"\n-> Testing with message: '{msg}'")
        try:
            result = run_agent(
                os.getcwd(),
                msg,
                on_event=lambda e: print(f"  Event: {e['type']}"),
            )
            print("[PASS] Nvidia response received")
        except Exception as e:
            print(f"[FAIL] Nvidia error: {e}")

    print("\n" + "=" * 60)
    print("TESTS COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
