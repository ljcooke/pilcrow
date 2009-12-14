#!/usr/bin/env python
"""
Static site generator.

Copyright (c) 2009 Liam Cooke
Licensed under the terms of the MIT license.

"""
import warnings
warnings.simplefilter('ignore', DeprecationWarning)

import sys

import pilcrow


if __name__ == '__main__':
    exitstatus = pilcrow.main()
    sys.exit(exitstatus or 0)
