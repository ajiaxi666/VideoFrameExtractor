import sys
from pathlib import Path

# Add the project root to Python path when the app is launched directly.
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from ui.main_window import main


if __name__ == "__main__":
    main()
