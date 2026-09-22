"""Compat entry: python main.py '<task>' -> agentguard wrap -- pi '<task>'"""
import sys

from agentguard.cli import main

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python main.py '<task prompt>'\n(better: agentguard wrap -- pi '<task prompt>')")
        sys.exit(1)
    sys.exit(main(["wrap", "--", "pi", sys.argv[1]]))
