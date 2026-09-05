# Notices and attribution

## UTM

This project contains a patch against UTM:

- Upstream: <https://github.com/utmapp/UTM>
- Base revision: `048ca7498ea3a374439149d51739d94c5300bcda`
- License: Apache License 2.0, as provided by UTM's upstream `LICENSE` file.

The UTM patch preserves upstream file headers. This repository does not redistribute the complete UTM source tree or compiled UTM binaries.

## CocoaSpice

This project contains a patch against CocoaSpice:

- Upstream: <https://github.com/utmapp/CocoaSpice>
- Base revision: `ff3fb176c8b7ff13acf9e3832347ec7ffab2fa1c`
- License: Apache License 2.0, as provided by CocoaSpice's upstream `LICENSE` file.

The CocoaSpice patch preserves upstream file headers. It adds a generic `CSFileTransfer` wrapper and does not modify spice-gtk.

## spice-gtk

The implementation calls existing spice-gtk file-transfer and port APIs. The spice-gtk upstream project is available at <https://gitlab.freedesktop.org/spice/spice-gtk> and provides its `COPYING` file under the GNU Lesser General Public License, version 2.1. This repository does not redistribute spice-gtk; users building UTM must continue to observe all upstream and transitive dependency licenses.

## Project-authored material

The newly authored guest helper, GNOME Shell extension, installation scripts, tests, and documentation in this standalone repository are licensed under the GNU Affero General Public License, version 3.0 only. The complete license text is in [LICENSE](LICENSE).

This project license does not relicense the upstream UTM or CocoaSpice source represented by the patches, nor spice-gtk or other dependencies. Those components retain their respective upstream licenses above.
