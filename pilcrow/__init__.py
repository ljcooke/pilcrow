"""
Static site generator.

Copyright (c) 2009 Liam Cooke
Licensed under the terms of the MIT license.

"""
from pilcrow import core, pages, util
from pilcrow.core import Pilcrow


def main():
    import optparse
    parser = optparse.OptionParser()

    parser.add_option('-x', '--clean', action='store_true', default=False)

    options, args = parser.parse_args()
    site_path = args and args[0] or '.'
    Pilcrow(site_path).build(clean=options.clean)
