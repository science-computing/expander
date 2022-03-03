#!/usr/bin/env python
""" A dummy peekaboo REST API """

import argparse
import asyncio
import base64
import binascii
import email.utils
import logging
import random
import signal
import urllib.parse

import sanic
import sanic.constants
import sanic.headers
import sanic.response
import schema

logger = logging.getLogger(__name__)


class DummyPeekabooAPI:
    """ extractor api """
    def __init__(self, host="127.0.0.1", port=8100, request_queue_size=100):
        logger.debug('Starting up server.')
        self.app = sanic.Sanic("PeekabooAV", configure_logging=False)
        self.app.config.FALLBACK_ERROR_FORMAT = "json"

        # silence sanic to a reasonable amount
        logging.getLogger('sanic.root').setLevel(logging.WARNING)
        logging.getLogger('sanic.access').setLevel(logging.WARNING)

        self.loop = asyncio.get_event_loop()
        self.server_coroutine = self.app.create_server(
            host=host, port=port, return_asyncio_server=True,
            backlog=request_queue_size,
            asyncio_server_kwargs=dict(start_serving=False))
        self.server = None

        # remember for diagnostics
        self.host = host
        self.port = port

        self.app.add_route(self.hello, '/')
        self.app.add_route(self.ping, '/ping')
        self.app.add_route(self.scan, "/v1/scan", methods=['POST'])
        self.app.add_route(self.scan2, "/v1/scan2", methods=['POST'])
        self.app.add_route(
            self.report, '/v1/report/<job_id:int>', methods=['GET'])

        self.jobs = []
        self.next_job_id = 1

    async def hello(self, _):
        """ hello endpoint as fallback and catch all

        @returns: hello world json response
        """
        return sanic.response.json({'hello': 'PeekabooAV'})

    async def ping(self, _):
        """ ping endpoint for diagnostics

        @returns: pong json response
        """
        return sanic.response.json({'answer': 'pong'})

    async def scan(self, request):
        """ scan endpoint for job submission

        @param request: sanic request object
        @type request: sanic.Request
        @returns: json response containing ID of newly created job
        """
        # this is sanic's multipart/form-data parser in a version that knows
        # that our file field contains binary data. This allows transferring
        # files without a filename. The generic parser would treat those as
        # text fields and try to decode them using the form charset or UTF-8 as
        # a fallback and cause errors such as: UnicodeDecodeError: 'utf-8'
        # codec can't decode byte 0xc0 in position 1: invalid start byte
        content_type, parameters = sanic.headers.parse_content_header(
            request.content_type)

        # application/x-www-form-urlencoded is inefficient at transporting
        # binary data. Also it needs a separate field to transfer the filename.
        # Make clear here that we do not support that format (yet).
        if content_type != 'multipart/form-data':
            logger.error('Invalid content type %s', content_type)
            return sanic.response.json(
                {'message': 'Invalid content type, use multipart/form-data'},
                400)

        boundary = parameters["boundary"].encode("utf-8")
        form_parts = request.body.split(boundary)
        # split above leaves preamble in form_parts[0] and epilogue in
        # form_parts[2]
        num_fields = len(form_parts) - 2
        if num_fields <= 0:
            logger.error('Invalid MIME structure in request, no fields '
                         'or preamble or epilogue missing')
            return sanic.response.json(
                {'message': 'Invalid MIME structure in request'}, 400)

        if num_fields != 1:
            logger.error('Invalid number of fields in form: %d', num_fields)
            return sanic.response.json(
                {'message': 'Invalid number of fields in form, we accept '
                    'only one field "file"'}, 400)

        form_part = form_parts[1]
        file_name = None
        content_type = None
        field_name = None
        line_index = 2
        line_end_index = 0
        while line_end_index != -1:
            line_end_index = form_part.find(b'\r\n', line_index)
            # this constitutes a hard requirement for the multipart headers
            # (and filenames therein) to be UTF-8-encoded. There are some
            # obscure provisions for transferring an encoding in RFC7578
            # section 5.1.2 for HTML forms which don't apply here so its
            # fallback to UTF-8 applies. This is no problem for our field name
            # (ASCII) and file names in RFC2231 encoding. For HTML5-style
            # percent-encoded filenames it means that whatever isn't
            # percent-encoded needs to be UTF-8 encoded. There are no rules in
            # HTML5 currently to percent-encode any UTF-8 byte sequences.
            form_line = form_part[line_index:line_end_index].decode('utf-8')
            line_index = line_end_index + 2

            if not form_line:
                break

            colon_index = form_line.index(':')
            idx = colon_index + 2
            form_header_field = form_line[0:colon_index].lower()

            # parse_content_header() reverts some of the percent encoding as
            # per HTML5 WHATWG spec. As it is a "living standard" (i.e. moving
            # target), it has changed over the years. There used to be
            # backslash doubling and explicit control sequence encoding. As of
            # this writing this has been changed to escaping only newline,
            # linefeed and double quote. Sanic only supports the double quote
            # part of that: %22 are reverted back to %. Luckily this interacts
            # reasonably well with RFC2231 decoding below since that would do
            # the same.
            #
            # There is no way to tell what version of the standard (or draft
            # thereof) the client was following when encoding. It seems accepted
            # practice in the browser world to just require current versions of
            # everything so their behaviour hopefully converges eventually.
            # This is also the reason why we do not try to improve upon it here
            # because it's bound to become outdated.
            #
            # NOTE: Since we fork the sanic code here we need to keep track of
            # its changes, particularly how it interacts with RFC2231 encoding
            # if escaping of the escape character %25 is ever added to the
            # HTML5 WHATWG spec. In that case parse_content_header() would
            # start breaking the RFC2231 encoding which would explain why its
            # use is forbidden in RFC7578 section 4.2 via RFC5987.
            form_header_value, form_parameters = sanic.headers.parse_content_header(
                form_line[idx:]
            )

            if form_header_field == 'content-disposition':
                field_name = form_parameters.get('name')
                file_name = form_parameters.get('filename')

                # non-ASCII filenames in RFC2231, "filename*" format
                if file_name is None and form_parameters.get('filename*'):
                    encoding, _, value = email.utils.decode_rfc2231(
                        form_parameters['filename*']
                    )
                    file_name = urllib.parse.unquote(value, encoding=encoding)
            elif form_header_field == 'content-type':
                content_type = form_header_value

        if field_name != 'file':
            logger.error('Field file missing from request')
            return sanic.response.json(
                {'message': 'Field "file" missing from request'}, 400)

        file_content = form_part[line_index:-4]
        content_disposition = request.headers.get('x-content-disposition')
        return await self.submit(file_content, file_name, content_type,
                                 content_disposition)

    async def scan2(self, request):
        """ scan endpoint for job submission using JSON structure to describe
        job

        @param request: sanic request object
        @type request: sanic.Request
        @returns: json response containing ID of newly created job
        """
        try:
            upload = schema.Schema({
                # RFC4648, no newlines, allow empty file
                'sample': schema.And(str, schema.Regex(r'^[A-Za-z0-9+/=]*$')),
                schema.Optional('file-name'): str,
                # RFC2183: 'attachment', 'inline' or any extension-token, the
                # latter being defined in RFC2045 as 1*<any (US-ASCII (RFC822
                # 0-127 decimal)) CHAR (32 decimal) except SPACE, CTLs (0-31
                # and 127 decimal), or tspecials with tspecials being "(" / ")"
                # / "<" / ">" / "@" / "," / ";" / ":" / "\" / <"> "/" / "[" /
                # "]" / "?" / "="
                schema.Optional('content-disposition'): schema.And(
                    str, schema.Regex(r'^[!#$%&\'*+-\.0-9<>A-Z^_`a-z~]+$')),
                # RFC2045: type "/" subtype with both possibly being extension
                # tokens
                schema.Optional('content-type'): schema.And(
                    str, schema.Regex(r'^[!#$%&\'*+-\./0-9<>A-Z^_`a-z~]+$')),
                }).validate(request.json)
        except schema.SchemaError as error:
            logger.warning("Invalid scan2 request: %s", error)
            return sanic.response.json(
                {'message': 'Invalid request'}, 400)

        try:
            file_content = base64.b64decode(upload['sample'], validate=True)
        except binascii.Error as error:
            logger.warning(
                "Invalid base64 encoding of sample content: %s", error)
            return sanic.response.json(
                {'message': 'Invalid base64 encoding of sample content'}, 400)

        file_name = upload.get('file-name')
        content_type = upload.get('content-type')
        content_disposition = upload.get('content-disposition')
        return await self.submit(file_content, file_name, content_type,
                                 content_disposition)

    async def submit(self, file_content, file_name, content_type,
                     content_disposition):
        """ Create and submit a sample as helper for scan endpoints.

        @param file_content: decoded binary sample file content
        @type file_content: bytes
        @param file_name: decoded file name
        @type file_name: str
        @param content_type: sample file content type
        @type content_type: str
        @param content_disposition: original sample content disposition from
                                    email
        @type content_disposition: str
        """
        del file_content
        del file_name
        del content_type
        del content_disposition

        job_id = self.next_job_id
        self.jobs.append(job_id)
        self.next_job_id += 1

        # send answer to client
        return sanic.response.json({'job_id': job_id}, 200)

    async def report(self, _, job_id):
        """ report endpoint for report retrieval by job ID

        @param request: sanic request object
        @type request: sanic.Request
        @param job_id: job ID extracted from endpoint path
        @type job_id: int
        @returns: report json response
        """
        if not job_id:
            return sanic.response.json(
                {'message': 'job ID missing from request'}, 400)

        # job has to be known, then it has a chance of 33% of being finished
        if job_id not in self.jobs or random.randint(0, 2):
            logger.debug('No analysis result yet for job %d', job_id)
            return sanic.response.json(
                {'message': 'No analysis result yet for job %d' % job_id}, 404)

        results = ["bad", "failed", "ignored", "unknown"]
        reasons = {
            "bad": [
                "Cuckoo score >= 4.0: 10.0",
                "The expression (3) classified the sample as Result.bad",
            ],
            "failed": [
                "Behavioral analysis by Cuckoo has produced an error and did "
                    "not finish successfully",
                "Rule aborted with error"
            ],
            "ignored": [
                "File type is on whitelist",
                "The expression (0) classified the sample as Result.ignored",
            ],
            "unknown": [
                "File type is not on the list of types to analyse",
                "File does not seem to exhibit recognizable malicious "
                    "behaviour",
            ],
        }

        result = results[random.randint(0, len(results) - 1)]
        reason = reasons[result][random.randint(0, len(reasons[result]) - 1)]

        return sanic.response.json({
            'result': result,
            'reason': reason,
            # FIXME: depends on saving the report to the database
            # 'report': report,
            }, 200)

    async def serve(self):
        """ Serves requests until shutdown is requested from the outside. """
        self.server = await self.server_coroutine

        # sanic 21.9 introduced an explicit startup that finalizes the app,
        # particularly the request routing. So we need to run it if present.
        if hasattr(self.server, 'startup'):
            await self.server.startup()

        await self.server.start_serving()
        logger.info('Peekaboo server is now listening on %s:%d',
                    self.host, self.port)
        await self.server.wait_closed()
        logger.debug('Server shut down.')

    def shut_down(self):
        """ Triggers a shutdown of the server, used by the signal handler and
        potentially other components to cause the main loop to exit. """
        logger.debug('Server shutdown requested.')
        if self.server is not None:
            self.server.close()


logging.basicConfig()
logger.setLevel(logging.DEBUG)

parser = argparse.ArgumentParser(description=DummyPeekabooAPI.__doc__)
parser.add_argument("--host", default="127.0.0.1", help="bind address")
parser.add_argument("--port", type=int, default=8100, help="bind port")
args = parser.parse_args()

api = DummyPeekabooAPI(args.host, args.port)


def signal_handler(sig):
    """ catch signal and call appropriate methods in registered listener
    classes """
    if sig in [signal.SIGINT, signal.SIGTERM]:
        logger.debug("SIGINT/TERM")
        api.shut_down()


async def async_main():
    """ asyncio entrypoint """
    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGINT, signal_handler, signal.SIGINT)
    loop.add_signal_handler(signal.SIGTERM, signal_handler, signal.SIGTERM)
    await api.serve()


def main():
    """ entrypoint """
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
