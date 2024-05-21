0. Consider whether this release will affect any customers, including those on
EMS, and warn them beforehand - in case they need to upgrade quickly.

1. Update the version number in pyproject.toml.
2. Set a variable to the version number for convenience:
   ```sh
   ver=x.y.z
   ```
3. Update the changelog:
   ```sh
   towncrier --version=$ver
   ```
4. Push your changes:
   ```sh
   git add -u && git commit -m $ver && git push
   ```
5. Sanity-check the
   [changelog](https://github.com/matrix-org/sygnal/blob/master/CHANGELOG.md)
   and update if need be.
6. Create a signed tag for the relese:
   ```sh
   git tag -s v$ver
   ```
   Base the tag message on the changelog.
7. Push the tag:
   ```sh
   git push origin tag v$ver
   ```
   Pushing a tag on GitHub will automatically trigger a build in Docker Hub and
   the resulting image will be published using the same tag as git.
8. Create release on GH project page:
   ```sh
   xdg-open https://github.com/matrix-org/sygnal/releases/edit/v$ver
   ```
9. Notify #sygnal:matrix.org, #synapse-dev:matrix.org and EMS that a new
   release has been published.
