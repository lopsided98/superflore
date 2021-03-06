import hashlib
import itertools
import os
import re
import tarfile
from typing import Dict, Iterable, Set

from rosdistro import DistributionFile
from rosdistro.dependency_walker import DependencyWalker
from rosdistro.rosdistro import RosPackage
from rosinstall_generator.distro import _generate_rosinstall

from superflore.exceptions import UnresolvedDependency
from superflore.generators.nix.nix_derivation import NixDerivation, NixLicense
from superflore.PackageMetadata import PackageMetadata
from superflore.utils import (download_file, get_distro_condition_context,
                              get_pkg_version, info, resolve_dep,
                              retry_on_exception, warn)


class NixPackage:
    """
    Retrieves the required metadata to define a Nix package derivation.
    """

    def __init__(self, name: str, distro: DistributionFile, tar_dir: str,
                 sha256_cache: Dict[str, str], all_pkgs: Set[str]) -> None:
        self.distro = distro
        self._all_pkgs = all_pkgs

        pkg = distro.release_packages[name]
        repo = distro.repositories[pkg.repository_name].release_repository
        ros_pkg = RosPackage(name, repo)

        rosinstall = _generate_rosinstall(name, repo.url,
                                          repo.get_release_tag(name), True)

        normalized_name = NixPackage.normalize_name(name)
        version = get_pkg_version(distro, name)
        src_uri = rosinstall[0]['tar']['uri']

        archive_path = os.path.join(tar_dir, '{}-{}-{}.tar.gz'
                                    .format(self.normalize_name(name),
                                            version, distro.name))

        downloaded_archive = False
        if os.path.exists(archive_path):
            info("using cached archive for package '{}'...".format(name))
        else:
            info("downloading archive version for package '{}'..."
                 .format(name))
            retry_on_exception(download_file, src_uri, archive_path,
                               retry_msg="network error downloading '{}'".format(src_uri),
                               error_msg="failed to download archive for '{}'".format(name))
            downloaded_archive = True

        if downloaded_archive or archive_path not in sha256_cache:
            sha256_cache[archive_path] = hashlib.sha256(
                open(archive_path, 'rb').read()).hexdigest()
        src_sha256 = sha256_cache[archive_path]

        # We already have the archive, so try to extract package.xml from it.
        # This is much faster than downloading it from GitHub.
        package_xml_regex = re.compile(r'^[^/]+/package\.xml$')
        package_xml = None
        archive = tarfile.open(archive_path, 'r|*')
        for file in archive:
            if package_xml_regex.match(file.name):
                package_xml = archive.extractfile(file).read()
                break
        # Fallback to the standard method of fetching package.xml
        if package_xml is None:
            warn("failed to extract package.xml from archive")
            package_xml = retry_on_exception(ros_pkg.get_package_xml,
                                             distro.name)

        metadata = PackageMetadata(package_xml)

        dep_walker = DependencyWalker(distro,
                                      get_distro_condition_context(
                                          distro.name))

        buildtool_deps = dep_walker.get_depends(pkg.name, "buildtool")
        buildtool_export_deps = dep_walker.get_depends(pkg.name, "buildtool_export")
        build_deps = dep_walker.get_depends(pkg.name, "build")
        build_export_deps = dep_walker.get_depends(pkg.name, "build_export")
        exec_deps = dep_walker.get_depends(pkg.name, "exec")
        test_deps = dep_walker.get_depends(pkg.name, "test")

        self.unresolved_dependencies = set()

        build_inputs = set(self._resolve_dependencies(build_deps))
        # buildtool_export_depends should probably be
        # propagatedNativeBuildInputs, but that causes many build failures.
        # Either ROS packages don't use it correctly or it doesn't map well to
        # Nix.
        propagated_build_inputs = self._resolve_dependencies(exec_deps | build_export_deps | buildtool_export_deps)
        build_inputs -= propagated_build_inputs

        check_inputs = self._resolve_dependencies(test_deps)
        check_inputs -= build_inputs

        native_build_inputs = self._resolve_dependencies(buildtool_deps)

        self._derivation = NixDerivation(
            name=normalized_name,
            version=version,
            src_url=src_uri,
            src_sha256=src_sha256,
            description=metadata.description,
            licenses=map(NixLicense, metadata.upstream_license),
            distro_name=distro.name,
            build_type=metadata.build_type,
            build_inputs=build_inputs,
            propagated_build_inputs=propagated_build_inputs,
            check_inputs=check_inputs,
            native_build_inputs=native_build_inputs)

    def _resolve_dependencies(self, deps: Iterable[str]) -> Set[str]:
        return set(itertools.chain.from_iterable(
            map(self._resolve_dependency, deps)))

    def _resolve_dependency(self, d: str) -> Iterable[str]:
        try:
            return (self.normalize_name(d),) \
                if d in self._all_pkgs \
                else resolve_dep(d, 'nix')[0]
        except UnresolvedDependency:
            self.unresolved_dependencies.add(d)
            return tuple()

    @staticmethod
    def normalize_name(name: str) -> str:
        """
        Convert underscores to dashes to match normal Nix package naming
        conventions.

        :param name: original package name
        :return: normalized package name
        """
        return name.replace('_', '-')

    @property
    def derivation(self):
        if self.unresolved_dependencies:
            raise UnresolvedDependency("failed to resolve dependencies!")

        return self._derivation
