import dataclasses
import datetime
import os
from typing import Optional
from xml.etree import ElementTree

from requests import post

import tmt
import tmt.steps
import tmt.steps.report
from tmt.utils import field

from .junit import make_junit_xml

DEFAULT_NAME = 'xunit.xml'


@dataclasses.dataclass
class ReportPolarionData(tmt.steps.report.ReportStepData):
    file: Optional[str] = field(
        default=None,
        option='--file',
        metavar='FILE',
        help='Path to the file to store xUnit in.'
        )

    upload: bool = field(
        default=True,
        option=('--upload / --no-upload'),
        is_flag=True,
        help="Whether to upload results to Polarion."
        )

    project_id: Optional[str] = field(
        default=None,
        option='--project-id',
        metavar='ID',
        help=(
            'Use specific Polarion project ID, '
            'also uses environment variable TMT_PLUGIN_REPORT_POLARION_PROJECT_ID.')
        )

    title: Optional[str] = field(
        default=None,
        option='--title',
        metavar='TITLE',
        help=(
            'Use specific test run title, '
            'also uses environment variable TMT_PLUGIN_REPORT_POLARION_TITLE.')
        )

    use_facts: bool = field(
        default=False,
        option=('--use-facts / --no-use-facts'),
        is_flag=True,
        help='Use hostname and arch from guest facts.'
        )

    planned_in: Optional[str] = field(
        default=None,
        option='--planned-in',
        metavar='PLANNEDIN',
        help=(
            'Select a specific release to mark this test run with, '
            'also uses environment variable TMT_PLUGIN_REPORT_POLARION_PLANNED_IN.')
        )

    assignee: Optional[str] = field(
        default=None,
        option='--assignee',
        metavar='ASSIGNEE',
        help=(
            'Who is responsible for this test run, '
            'also uses environment variable TMT_PLUGIN_REPORT_POLARION_ASSIGNEE.')
        )

    pool_team: Optional[str] = field(
        default=None,
        option='--pool-team',
        metavar='POOLTEAM',
        help=(
            'Which subsystem is this test run relevant for, '
            'also uses environment variable TMT_PLUGIN_REPORT_POLARION_POOL_TEAM')
        )

    arch: Optional[str] = field(
        default=None,
        option='--arch',
        metavar='ARCH',
        help=(
            'Which architecture was this run executed on, '
            'also uses environment variable TMT_PLUGIN_REPORT_POLARION_ARCH.')
        )

    platform: Optional[str] = field(
        default=None,
        option='--platform',
        metavar='PLATFORM',
        help=(
            'Which platform was this run executed on, '
            'also uses environment variable TMT_PLUGIN_REPORT_POLARION_PLATFORM.')
        )

    build: Optional[str] = field(
        default=None,
        option='--build',
        metavar='BUILD',
        help=(
            'Which build was this run executed on, '
            'also uses environment variable TMT_PLUGIN_REPORT_POLARION_BUILD.')
        )

    sample_image: Optional[str] = field(
        default=None,
        option='--sample-image',
        metavar='SAMPLEIMAGE',
        help=(
            'Which sample image was this run executed on, '
            'also uses environment variable TMT_PLUGIN_REPORT_POLARION_SAMPLE_IMAGE.')
        )

    logs: Optional[str] = field(
        default=None,
        option='--logs',
        metavar='LOGLOCATION',
        help=(
            'Location of the logs for this test run, '
            'also uses environment variable TMT_PLUGIN_REPORT_POLARION_LOGS.')
        )

    compose_id: Optional[str] = field(
        default=None,
        option='--compose-id',
        metavar='COMPOSEID',
        help=(
            'Compose ID of image used for this run, '
            'also uses environment variable TMT_PLUGIN_REPORT_POLARION_COMPOSE_ID.')
        )


@tmt.steps.provides_method('polarion')
class ReportPolarion(tmt.steps.report.ReportPlugin):
    """
    Write test results into a xUnit file and upload to Polarion
    """

    _data_class = ReportPolarionData

    def prune(self, logger: tmt.log.Logger) -> None:
        """ Do not prune generated xunit report """
        pass

    def go(self) -> None:
        """ Go through executed tests and report into Polarion """
        super().go()

        from tmt.export.polarion import find_polarion_case_ids, import_polarion
        import_polarion()
        from tmt.export.polarion import PolarionWorkItem
        assert PolarionWorkItem

        title = self.get(
            'title',
            os.getenv(
                'TMT_PLUGIN_REPORT_POLARION_TITLE',
                self.step.plan.name.rsplit('/', 1)[1] +
                # Polarion server running with UTC timezone
                datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y%m%d%H%M%S")))
        title = title.replace('-', '_')
        project_id = self.get('project-id', os.getenv('TMT_PLUGIN_REPORT_POLARION_PROJECT_ID'))
        upload = self.get('upload')
        use_facts = self.get('use-facts')
        other_testrun_fields = [
            'planned_in', 'assignee', 'pool_team', 'arch', 'platform', 'build', 'sample_image',
            'logs', 'compose_id']

        junit_suite = make_junit_xml(self)
        xml_tree = ElementTree.fromstring(junit_suite.to_xml_string([junit_suite]))

        properties = {
            'polarion-project-id': project_id,
            'polarion-user-id': PolarionWorkItem._session.user_id,
            'polarion-testrun-title': title,
            'polarion-project-span-ids': project_id}
        for tr_field in other_testrun_fields:
            param = self.get(tr_field, os.getenv(f'TMT_PLUGIN_REPORT_POLARION_{tr_field.upper()}'))
            # TODO: remove the os.getenv when envvars in click work with steps in plans as well
            # as with steps on cmdline
            if param:
                properties[f"polarion-custom-{tr_field.replace('_', '')}"] = param
        if use_facts:
            properties['polarion_custom_hostname'] = self.step.plan.provision.guests()[0].guest
            properties['polarion_custom_arch'] = self.step.plan.provision.guests()[0].facts.arch
        testsuites_properties = ElementTree.SubElement(xml_tree, 'properties')
        for name, value in properties.items():
            ElementTree.SubElement(testsuites_properties, 'property', attrib={
                'name': name, 'value': value})

        testsuite = xml_tree.find('testsuite')
        project_span_ids = xml_tree.find(
            '*property[@name="polarion-project-span-ids"]')

        for result in self.step.plan.execute.results():
            if not result.ids or not any(result.ids.values()):
                raise tmt.utils.ReportError(
                    f"Test Case {result.name} is not exported to Polarion, "
                    "please run 'tmt tests export --how polarion' on it")
            work_item_id, test_project_id = find_polarion_case_ids(result.ids)

            if test_project_id is None:
                raise tmt.utils.ReportError("Test case missing or not found in Polarion")

            assert work_item_id is not None
            assert project_span_ids is not None

            if test_project_id not in project_span_ids.attrib['value']:
                project_span_ids.attrib['value'] += f',{test_project_id}'

            test_properties = {
                'polarion-testcase-id': work_item_id,
                'polarion-testcase-project-id': test_project_id}

            assert testsuite is not None
            test_case = testsuite.find(f"*[@name='{result.name}']")
            assert test_case is not None
            properties_elem = ElementTree.SubElement(test_case, 'properties')
            for name, value in test_properties.items():
                ElementTree.SubElement(properties_elem, 'property', attrib={
                    'name': name, 'value': value})

        assert self.workdir is not None

        f_path = self.get("file", self.workdir / DEFAULT_NAME)
        with open(f_path, 'wb') as fw:
            ElementTree.ElementTree(xml_tree).write(fw)

        if upload:
            server_url = str(PolarionWorkItem._session._server.url)
            polarion_import_url = (
                f'{server_url}{"" if server_url.endswith("/") else "/"}'
                'import/xunit')
            auth = (
                PolarionWorkItem._session.user_id,
                PolarionWorkItem._session.password)

            response = post(
                polarion_import_url, auth=auth,
                files={'file': ('xunit.xml', ElementTree.tostring(xml_tree))})
            self.info(
                f'Response code is {response.status_code} with text: {response.text}')
        else:
            self.info("Polarion upload can be done manually using command:")
            self.info(
                "curl -k -u <USER>:<PASSWORD> -X POST -F file=@<XUNIT_XML_FILE_PATH> "
                "<POLARION_URL>/polarion/import/xunit")
        self.info(f"xUnit file saved at: {f_path}")
