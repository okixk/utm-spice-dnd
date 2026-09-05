# Host cleanup

The build is isolated to this repository:

```sh
rm -rf .build dist
```

This removes only this project's build workspace and output. It does not remove or modify `/Applications/UTM.app`, UTM's normal documents, or any VM disk.
