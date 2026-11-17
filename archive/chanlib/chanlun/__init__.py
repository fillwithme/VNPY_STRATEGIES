import os
import sys
current_dir = os.path.abspath(os.path.dirname(__file__))
import platform
p = str(platform.system())
if p == 'Windows':  # windows下
    os.environ['path'] += ';{}'.format(current_dir)
else:  # linux 下
    if current_dir not in sys.path:
        sys.path.append(current_dir)

from .pyChanlun import ChanGraph, ChanLibrary
