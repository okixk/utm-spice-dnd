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

The CocoaSpice patch preserves upstream file headers. It adds a generic
`CSFileTransfer` wrapper; it is separate from the spice-gtk patch described
below.

## spice-gtk

This project contains a patch against spice-gtk:

- Patch: `host/virt-manager-linux/patches/spice-gtk-0.42-semantic-dnd.patch`
- Upstream: <https://gitlab.freedesktop.org/spice/spice-gtk>
- Base release: `0.42`
- Corresponding upstream revision: `f04479c16f0969fb394ebe74b6eff74e560a42f0`
- License: GNU Lesser General Public License, version 2.1 or (at your option)
  any later version, consistent with spice-gtk's upstream source notices and
  `COPYING` file.

The patch changes upstream spice-gtk files and adds `semantic-dnd.c`,
`semantic-dnd.h`, and the associated C tests. The modifications represented by
that patch, including those new files, are offered under the same
LGPL-2.1-or-later terms so that the patched library remains under spice-gtk's
license. Existing upstream copyright and license notices remain in force.

The development installer downloads the pinned upstream source, applies the
patch, and builds it into a private user prefix. This repository does not
contain the complete spice-gtk source tree or distribute compiled spice-gtk
binaries. Anyone conveying a patched build remains responsible for satisfying
the LGPL and all applicable dependency licenses.

## Project-authored material

Except for material covered by the upstream-component terms above, the newly
authored guest helper, GNOME Shell extension, installation scripts, tests, and
documentation in this standalone repository are licensed under the GNU Affero
General Public License, version 3.0 only. The complete license text is in
[LICENSE](LICENSE).

The project-wide AGPL license does not relicense the upstream UTM, CocoaSpice,
or spice-gtk source represented by the patches, nor any other dependency.
Those components and their derivative patches retain the respective terms
identified above.
