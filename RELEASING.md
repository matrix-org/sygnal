0. Consider whether this release will affect any customers, including those on
EMS, and warn them beforehand - in case they need to upgrade quickly.

1. Set a variable to the version number for convenience:
   ```sh
   ver=x.y.z
   ```
1. Update the changelog:
   ```sh
   towncrier --version=$ver
   ```
1. Push your changes:
   ```sh
   git add -u && git commit -m $ver && git push
   ```
1. Sanity-check the
   [changelog](https://github.com/matrix-org/sygnal/blob/master/CHANGELOG.md)
   and update if need be.
1. Create a signed tag for the relese:
   ```sh
   git tag -s v$ver
   ```
   Base the tag message on the changelog.
1. Push the tag:
   ```sh
   git push origin tag v$ver
   ```
   Pushing a tag on GitHub will automatically trigger a build in Docker Hub and
   the resulting image will be published using the same tag as git.
1. Create release on GH project page:
   ```sh
   xdg-open https://github.com/matrix-org/sygnal/releases/edit/v$ver
   ```
1. Notify #sygnal:matrix.org, #synapse-dev:matrix.org and EMS that a new
   release has been published.
