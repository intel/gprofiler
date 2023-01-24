#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import datetime
import gzip
import json
from io import BytesIO
from typing import IO, Any, Dict, List, Optional, Tuple, cast

import requests
from requests import Session

from gprofiler import __version__
from gprofiler.exceptions import APIError
from gprofiler.log import get_logger_adapter
from gprofiler.utils import get_iso8601_format_time

logger = get_logger_adapter(__name__)

GRANULATE_SERVER_HOST = "https://profiler.granulate.io"
DEFAULT_REQUEST_TIMEOUT = 5
DEFAULT_UPLOAD_TIMEOUT = 120


class SparkAPIClient:
    BASE_PATH = "api"  # tbd

    def __init__(
        self,
        host: str,
        key: str,
        service: str,
        hostname: str,
        curlify_requests: bool,
        upload_timeout: int,
        version: str = "v0",
    ):
        self._host: str = host
        self._upload_timeout = upload_timeout
        self._version: str = version
        self._key = key
        self._service = service
        self._hostname = hostname
        self._curlify = curlify_requests

        self._init_session()
        logger.info(f"The connection to the server was successfully established (service {service!r})")

    def _init_session(self) -> None:
        self._session: Session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {self._key}", "X-Gprofiler-Service": self._service})

        # Raises on failure
        self.get_health()

    def get_base_url(self, api_version: str = None) -> str:
        version = api_version if api_version is not None else self._version
        return "{}/{}/{}".format(self._host.rstrip("/"), self.BASE_PATH, version)

    def _get_query_params(self) -> List[Tuple[str, str]]:
        return [
            ("key", self._key),
            ("service", self._service),
            ("hostname", self._hostname),
            ("timestamp", get_iso8601_format_time(datetime.datetime.utcnow())),
        ]

    def _send_request(
        self,
        method: str,
        path: str,
        data: Any,
        files: Dict = None,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
        api_version: str = None,
        params: Dict[str, str] = None,
    ) -> Dict:
        opts: dict = {"headers": {}, "files": files, "timeout": timeout}
        if params is None:
            params = {}

        if method.upper() == "GET":
            if data is not None:
                params.update(data)
        else:
            opts["headers"]["Transfer-Encoding"] = "gzip"
            opts["headers"]["Content-type"] = "application/json"
            buffer = BytesIO()
            with gzip.open(buffer, mode="wt", encoding="utf-8") as gzip_file:
                try:
                    json.dump(data, cast(IO[str], gzip_file), ensure_ascii=False)
                except TypeError:
                    # This should only happen while in development, and is used to get a more indicative error.
                    bad_json = str(data)
                    logger.exception("Given data is not a valid JSON!", bad_json=bad_json)
                    raise
            opts["data"] = buffer.getvalue()

        opts["params"] = self._get_query_params() + [(k, v) for k, v in params.items()]

        resp = self._session.request(method, "{}/{}".format(self.get_base_url(api_version), path), **opts)
        if self._curlify:
            import curlify  # type: ignore # import here as it's not always required.

            if resp.request.body is not None:
                # curlify attempts to decode bytes into utf-8. Our content is gzipped so we undo the gzip here
                # (it's fine to edit the object, as the request was already sent).
                assert resp.request.headers["Content-Encoding"] == "gzip"  # make sure it's really gzip before we undo
                assert isinstance(resp.request.body, bytes)
                resp.request.body = gzip.decompress(resp.request.body)
                del resp.request.headers["Content-Encoding"]
            logger.debug(
                "API request",
                curl_command=curlify.to_curl(resp.request),
                status_code=resp.status_code
                no_server_log=True,
            )

        if 400 <= resp.status_code < 500:
            try:
                response_data = resp.json()
                assert isinstance(response_data, json)
                logger.debug(response_data)
                logger.debug(resp)
                raise APIError(response_data.get("message", "(no message in response)"), response_data)
            except ValueError:
                raise APIError(resp.text)
        else:
            resp.raise_for_status()
        return cast(dict, resp.json())

    def get(self, path: str, data: Any = None, **kwargs: Any) -> Dict:
        return self._send_request("GET", path, data, **kwargs)

    def post(self, path: str, data: Any = None, **kwargs: Any) -> Dict:
        return self._send_request("POST", path, data, **kwargs)

    def put(self, path: str, data: Any = None, **kwargs: Any) -> Dict:
        return self._send_request("PUT", path, data, **kwargs)

    def patch(self, path: str, data: Any = None, **kwargs: Any) -> Dict:
        return self._send_request("PATCH", path, data, **kwargs)

    def delete(self, path: str, data: Any = None, **kwargs: Any) -> Dict:
        return self._send_request("DELETE", path, data, **kwargs)

    def get_health(self) -> Dict:
        return self.get("health_check")

    def submit_spark_metrics(
        self, timestamp: int, metrics: List[Dict[str, Any]], spark_api_version: Optional[str] = None
    ) -> Dict:
        return self.post(
            "spark_metrics",  # TODO: Change? TBD
            {
                "format_version": "0",
                "timestamp": timestamp,
                "metrics": metrics,
            },
            timeout=self._upload_timeout,
            api_version="v0" if spark_api_version is None else spark_api_version,
            params={"version": __version__},
        )
