# generates ebuilds from ros distribution data
import sys
import os
import xmltodict
import glob
from termcolor import colored

from rosinstall_generator.distro import get_distro, get_package_names, _generate_rosinstall
from rosdistro.dependency_walker import DependencyWalker, SourceDependencyWalker
from rosdistro.manifest_provider import get_release_tag
from rosdistro.rosdistro import RosPackage

from .ebuild import Ebuild, UnresolvedDependency
from .metadata_xml import metadata_xml

org = "Open Source Robotics Foundation"
org_license = "BSD"

def warn(string):
    print(colored(string, 'yellow'))

def ok(string):
    print(colored(string, 'green'))

def err(string):
    print(colored(string, 'red'))
    
def make_dir(dirname):
    try:
        os.makedirs(dirname)
    except:
        pass

def get_pkg_version(distro, pkg_name):
    pkg = distro.release_packages[pkg_name]
    repo = distro.repositories[pkg.repository_name].release_repository
    maj_min_patch, deb_inc = repo.version.split('-')
    if deb_inc != '0':        
        return '{0}-r{1}'.format(maj_min_patch, deb_inc)
    return maj_min_patch
    
def generate_installers(distro_name, overlay, preserve_existing=True):
    make_dir("ros-{}".format(distro_name))
    distro = get_distro(distro_name)
    pkg_names = get_package_names(distro)
    total = float(len(pkg_names[0]))
    borkd_pkgs = dict()
    changes = []
    installers = []
    bad_installers = []
    succeeded = 0
    failed = 0

    for i, pkg in enumerate(sorted(pkg_names[0])):
        version = get_pkg_version(distro, pkg)        
        ebuild_exists = os.path.exists("ros-{}/{}/{}-{}.ebuild".format(distro_name, pkg, pkg, version))
        has_patches = os.path.exists("ros-{}/{}/files".format(distro_name, pkg))
        percent = '%.1f' % (100 * (float(i) / total))

        if preserve_existing and ebuild_exists:
            ok(">>>> {0}%: Ebuild for package {1} up to date, skipping...".format(percent, pkg))
            succeeded = succeeded + 1
            continue
        # otherwise, remove a (potentially) existing ebuild.
        existing = glob.glob('ros-{0}/{1}/*.ebuild'.format(distro_name, pkg))
        if len(existing) > 0:
            overlay.remove_file(existing[0])
        try:
            current = gentoo_installer(distro, pkg, has_patches)
        except Exception as e:
            err('!!!! Failed to generate gentoo installer for package {}!'.format(pkg))
            err('!!!!   exception: {0}'.format(e))
            failed = failed + 1
            continue
        try:
            ebuild_text = current.ebuild_text()
            metadata_text = current.metadata_text()
        except UnresolvedDependency as msg:
            err("!!!! Failed to resolve required dependencies for package {}!".format(pkg))
            unresolved = current.ebuild.get_unresolved()
            borkd_pkgs[pkg] = list()
            for dep in unresolved:
                err("!!!!  unresolved: \"{}\"".format(dep))
                borkd_pkgs[pkg].append(dep)
            err("!!!! Failed to generate gentoo installer for package {}!".format(pkg))
            failed = failed + 1
            continue # do not generate an incomplete ebuild
        except KeyError as key:
            err("!!!! Failed to parse data for package {}!".format(pkg))
            unresolved = current.ebuild.get_unresolved()
            err("!!!! Failed to generate gentoo installer for package {}!".format(pkg))
            failed = failed + 1
            continue # do not generate an incomplete ebuild            
        make_dir("ros-{}/{}".format(distro_name, pkg))
        ok(">>>> {0}%: Succesfully generated installer for package \"{1}.\"".format(percent, pkg))
        succeeded = succeeded + 1

        try:
            ebuild_file = open('ros-{0}/{1}/{1}-{2}.ebuild'.format(distro_name, pkg, version), "w")
            metadata_file = open('ros-{0}/{1}/metadata.xml'.format(distro_name, pkg), "w")

            ebuild_file.write(ebuild_text)
            metadata_file.write(metadata_text)
            changes.append('*{0} --> {1}*'.format(pkg, version))

        except:
            err("!!!! Failed to write ebuild/metadata to disk!")
            installers.append(current)
            err("!!!! {0}%: Failed to generate gentoo installer for package {1}!".format(percent, pkg))
            bad_installers.append(current)
            failed = failed + 1
        
    print("------ Generated {} / {} installers for distro \"{}\" ------".format(succeeded, failed + succeeded, distro_name))
    print()

    if len(borkd_pkgs) > 0:
        warn(">>>> Unresolved:")
        for broken in borkd_pkgs.keys():
            warn(">>>> {}:".format(broken))
            warn(">>>>   {}".format(borkd_pkgs[broken]))

    return installers, borkd_pkgs, changes
    
def _gen_metadata_for_package(distro, pkg_name, pkg, repo, ros_pkg, pkg_rosinstall):
    pkg_metadata_xml = metadata_xml()        
    try:    
        pkg_xml = ros_pkg.get_package_xml(distro.name)
    except Exception as e:
        warn(">>>> cannot fetch metadata for package {}".format(pkg_name))
        return pkg_metadata_xml
    pkg_fields = xmltodict.parse(pkg_xml)

    if 'maintainer' in pkg_fields['package']:
        if isinstance(pkg_fields['package']['maintainer'], list):
            pkg_metadata_xml.upstream_email = pkg_fields['package']['maintainer'][0]['@email']
            pkg_metadata_xml.upstream_name  = pkg_fields['package']['maintainer'][0]['#text']
        elif isinstance(pkg_fields['package']['maintainer']['@email'], list):
            pkg_metadata_xml.upstream_email = pkg_fields['package']['maintainer'][0]['@email']
            pkg_metadata_xml.upstream_name = pkg_fields['package']['maintainer'][0]['#text']
        else:
            pkg_metadata_xml.upstream_email = pkg_fields['package']['maintainer']['@email']
            if '#text' in pkg_fields['package']['maintainer']:
                pkg_metadata_xml.upstream_name = pkg_fields['package']['maintainer']['#text']
            else:
                pkg_metadata_xml.upstream_name = "UNKNOWN"

    pkg_metadata_xml.upstream_bug_url = repo.url.replace("-release", "").replace(".git", "/issues")

    """
    @todo: longdescription
    """
    return pkg_metadata_xml

def _gen_ebuild_for_package(distro, pkg_name, pkg, repo, ros_pkg, pkg_rosinstall):
    pkg_ebuild = Ebuild()

    pkg_ebuild.distro = distro.name
    pkg_ebuild.src_uri = pkg_rosinstall[0]['tar']['uri']
    pkg_names = get_package_names(distro)
    pkg_dep_walker = DependencyWalker(distro)

    pkg_buildtool_deps = pkg_dep_walker.get_depends(pkg_name, "buildtool")
    pkg_build_deps     = pkg_dep_walker.get_depends(pkg_name, "build")
    pkg_run_deps       = pkg_dep_walker.get_depends(pkg_name, "run")
    
    pkg_keywords = [ 'x86', 'amd64', 'arm', '~arm64' ]
    
    # add run dependencies
    for rdep in pkg_run_deps:
        pkg_ebuild.add_run_depend(rdep, rdep in pkg_names[0])

    # add build dependencies
    for bdep in pkg_build_deps:
        pkg_ebuild.add_build_depend(bdep, bdep in pkg_names[0])

    # add build tool dependencies
    for tdep in pkg_buildtool_deps:
        pkg_ebuild.add_build_depend(tdep, tdep in pkg_names[0])

    # add keywords
    for key in pkg_keywords:
        pkg_ebuild.add_keyword(key)

    # parse throught package xml
    try:    
        pkg_xml = ros_pkg.get_package_xml(distro.name)
    except Exception as e:
        warn(">>>> cannot fetch metadata for package {}".format(pkg_name))
        return pkg_ebuild
    pkg_fields = xmltodict.parse(pkg_xml)
    
    pkg_ebuild.upstream_license = pkg_fields['package']['license']
    pkg_ebuild.description = pkg_fields['package']['description']
    if isinstance(pkg_ebuild.description, str):
        pkg_ebuild.description = pkg_ebuild.description.replace('`', "")
    if len(pkg_ebuild.description) > 80:
        pkg_ebuild.description = pkg_ebuild.description[:80]
    try:
        if 'url' not in pkg_fields['package']:
            warn(">>>> no website field for package {}".format(pkg_name))
        elif sys.version_info <= (3, 0):
            if isinstance(pkg_fields['package']['url'], unicode):
                pkg_ebuild.homepage = pkg_fields['package']['url']
        elif isinstance(pkg_fields['package']['url'], str):
            pkg_ebuild.homepage = pkg_fields['package']['url']
        elif '@type' in pkg_fields['package']['url']:
            if pkg_fields['package']['url']['@type'] == 'website':
                if '#text' in pkg_fields['package']['url']:
                    pkg_ebuild.homepage = pkg_fields['package']['url']['#text']
        else:
            warn(">>>> failed to parse website field for package {}".format(pkg_name))
    except TypeError as e:
        warn(">>>> failed to parse website package {}: {}".format(pkg_name, e))    

    """
    @todo: homepage
    """
    return pkg_ebuild

class gentoo_installer(object):
    def __init__(self, distro, pkg_name, has_patches=False):
        pkg = distro.release_packages[pkg_name]
        repo = distro.repositories[pkg.repository_name].release_repository
        ros_pkg = RosPackage(pkg_name, repo)

        pkg_rosinstall = _generate_rosinstall(pkg_name, repo.url, get_release_tag(repo, pkg_name), True)

        self.metadata_xml = _gen_metadata_for_package(distro, pkg_name, pkg, repo, ros_pkg, pkg_rosinstall)
        self.ebuild       = _gen_ebuild_for_package(distro, pkg_name, pkg, repo, ros_pkg, pkg_rosinstall)
        self.ebuild.has_patches = has_patches

    def metadata_text(self):
        return self.metadata_xml.get_metadata_text()

    def ebuild_text(self):
        return self.ebuild.get_ebuild_text(org, org_license)