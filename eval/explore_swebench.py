"""Inspect the SWE-bench Lite dataset schema before building anything against it."""
from datasets import load_dataset

FIELDS = ["instance_id", "repo", "base_commit", "problem_statement", "FAIL_TO_PASS", "PASS_TO_PASS"]


def main():
    dataset = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
    print(f"Loaded {len(dataset)} instances")
    for instance in dataset.select(range(3)):
        print("=" * 80)
        for field in FIELDS:
            value = str(instance[field])
            print(f"{field}: {value[:300]}")


if __name__ == "__main__":
    main()
