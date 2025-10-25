import sys
import os

# Get the absolute path of the src subfolder
src_subfolder_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../src', 'aye'))

# Add the src subfolder to the system path
sys.path.append(src_subfolder_path)

