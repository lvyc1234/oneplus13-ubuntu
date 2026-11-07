#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Stage the isolated IR driver source; never compile, test or enable a node."""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    target = args.kernel.resolve() / 'drivers/media/rc'
    lock = json.loads((HERE / 'sources.json').read_text())
    for name, expected in lock['source_files'].items():
        if hashlib.sha256((HERE / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Changed infrared source: ' + name)
    for name, expected in lock['base_files'].items():
        if hashlib.sha256((target / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Unexpected RC base source: ' + name)
    driver = target / 'ir-dodge-spi.c'
    if driver.exists() or args.report.exists():
        raise ValueError('Preserve existing source preparation')
    kconfig = (target / 'Kconfig').read_text()
    marker = 'endif #RC_CORE'
    if kconfig.count(marker) != 1:
        raise ValueError('Ambiguous rc-core Kconfig boundary')
    block = '''config IR_DODGE_SPI
	tristate "Experimental OnePlus 13 SPI infrared transmitter"
	depends on SPI && OF && REGULATOR && PINCTRL
	help
	  Bounded rc-core transmitter candidate for the exact OnePlus 13 wiring.
	  Requires an explicit experimental DT node and compatible supply votes.
	  It is not enabled by the normal phone configuration.

'''
    driver.write_bytes((HERE / 'ir-dodge-spi.c').read_bytes())
    (target / 'Kconfig').write_text(kconfig.replace(marker, block + marker))
    with (target / 'Makefile').open('a') as stream:
        stream.write('\nobj-$(CONFIG_IR_DODGE_SPI) += ir-dodge-spi.o\n')
    report = {'scope': 'IR_SOURCE_PREPARATION_ONLY', 'compiled': False,
              'tests_executed': False, 'phone_operations': 0,
              'board_node_enabled': False, 'hardware_qualified': False,
              'files': {name: hashlib.sha256((target / name).read_bytes()).hexdigest()
                        for name in ('Kconfig', 'Makefile', 'ir-dodge-spi.c')}}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
