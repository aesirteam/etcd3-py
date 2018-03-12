"""
synchronous client
"""

import json

import requests
import six
from six.moves import urllib_parse

from .baseclient import BaseClient
from .baseclient import BaseModelizedStreamResponse
from .errors import Etcd3Exception
from .errors import Etcd3StreamError
from .errors import get_client_error


class ModelizedStreamResponse(BaseModelizedStreamResponse):
    """
    Model of a stream response
    """

    def __init__(self, method, resp, decode=True):
        """
        :param resp: Response
        """
        self.resp = resp
        self.decode = decode
        self.method = method

    def close(self):
        """
        close the stream
        """
        return self.resp.close()

    def __iter__(self):
        for data in iter_response(self.resp):
            if not data:
                continue
            data = json.loads(data)
            if data.get('error'):
                # {"error":{"grpc_code":14,"http_code":503,"message":"rpc error: code = Unavailable desc = transport is closing","http_status":"Service Unavailable"}}
                err = data.get('error')
                raise Etcd3APIError(err.get('message'), code=err.get('code'), status=err.get('http_code'))
            if 'result' in data:
                data = data.get('result', {})  # the real data is put under the key: 'result'
            yield Client._modelizeResponseData(self.method, data, decode=self.decode)


def iter_response(resp):
    """
    yield response content by every json object
    we don't yield by line, because the content of etcd's gRPC-JSON-Gateway stream response
    does not have a delimiter between each object by default. (only one line)

    https://github.com/grpc-ecosystem/grpc-gateway/pull/497/files

    :param resp: Response
    :return: dict
    """
    buf = []
    bracket_flag = 0
    for c in resp.iter_content(chunk_size=1):
        if six.PY3:
            c = six.text_type(c, encoding='utf-8')
        buf.append(c)
        if c == '{':
            bracket_flag += 1
        elif c == '}':
            bracket_flag -= 1
        if bracket_flag == 0:
            s = ''.join(buf)
            buf = []
            yield s
        elif bracket_flag < 0:
            raise Etcd3StreamError("Stream decode error", buf, resp)
    if buf:
        raise Etcd3StreamError("Stream decode error", buf, resp)


class Client(BaseClient):
    def __init__(self, host='localhost', port=2379, protocol='http',
                 ca_cert=None, cert_key=None, cert_cert=None,
                 timeout=None, headers=None, user_agent=None, pool_size=30,
                 username=None, password=None, token=None):
        super(Client, self).__init__(host=host, port=port, protocol=protocol,
                                     ca_cert=ca_cert, cert_key=cert_key, cert_cert=cert_cert,
                                     timeout=timeout, headers=headers, user_agent=user_agent, pool_size=pool_size,
                                     username=username, password=password, token=token)
        self._session = requests.session()
        self.__set_conn_pool(pool_size)

    def __set_conn_pool(self, pool_size):
        from requests.adapters import HTTPAdapter
        adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size)
        self._session.mount('http://', adapter)
        self._session.mount('https://', adapter)

    def close(self):
        """
        close all connections in connection pool
        """
        return self._session.close()

    @property
    def baseurl(self):
        """
        :return: baseurl from protocol, host, self
        """
        return '{}://{}:{}'.format(self.protocol, self.host, self.port)

    def _url(self, method):
        return urllib_parse.urljoin(self.baseurl, method)

    @classmethod
    def _modelizeStreamResponse(cls, method, resp, decode=True):
        return ModelizedStreamResponse(method, resp, decode)

    @staticmethod
    def _raise_for_status(resp):
        status = resp.status_code
        if status < 400:
            return
        try:
            data = resp.json()
        except Exception:
            error = resp.content
            code = 2
        else:
            error = data.get('error')
            code = data.get('code')
        raise get_client_error(error, code, status, resp)

    def _get(self, url, **kwargs):
        r"""
        Sends a GET request. Returns :class:`Response` object.

        :param url: URL for the new :class:`Request` object.
        :param \*\*kwargs: Optional arguments that ``request`` takes.
        :rtype: requests.Response
        """
        return self._session.get(url, **kwargs)

    def _post(self, url, data=None, json=None, **kwargs):
        r"""
        Sends a POST request. Returns :class:`Response` object.

        :param url: URL for the new :class:`Request` object.
        :param data: (optional) Dictionary, bytes, or file-like object to send in the body of the :class:`Request`.
        :param json: (optional) json to send in the body of the :class:`Request`.
        :param \*\*kwargs: Optional arguments that ``request`` takes.
        :rtype: requests.Response
        """
        return self._session.post(url, data=data, json=json, **kwargs)

    def call_rpc(self, method, data=None, stream=False, encode=True, raw=False, **kwargs):
        """
        call ETCDv3 RPC and return response object

        :type method: str
        :param method: the rpc method, which is a path of RESTful API
        :type data: dict
        :param data: request payload to be post to ETCD's gRPC-JSON-Gateway default: {}
        :type stream: bool
        :param stream: whether return a stream response object, default: False
        :type encode: bool
        :param encode: whether encode the data before post, default: True
        :param kwargs: additional params to pass to the http request, like headers, timeout etc.
        :return: Etcd3RPCResponseModel or Etcd3StreamingResponse
        """
        data = data or {}
        kwargs.setdefault('timeout', self.timeout)
        kwargs.setdefault('cert', self.cert)
        if self.token:
            kwargs.setdefault('headers', {}).setdefault('authorization', self.token)
        kwargs.setdefault('headers', {}).setdefault('user_agent', self.user_agent)
        kwargs.setdefault('headers', {}).update(self.headers)
        if encode:
            data = self._encodeRPCRequest(method, data)
        resp = self._post(self._url(method), json=data or {}, stream=stream, **kwargs)
        self._raise_for_status(resp)
        if raw:
            return resp
        if stream:
            try:
                return self._modelizeStreamResponse(method, resp)
            except Etcd3Exception:
                resp.close()
        return self._modelizeResponseData(method, resp.json())

    def auth(self, username=None, password=None):
        """
        call auth.authenticate and save the token

        :type username: str
        :param username: username
        :type password: str
        :param password: password
        """
        username = username or self.username
        password = password or self.password
        r = self.authenticate(username, password)
        self.username = username
        self.password = password
        self.token = r.token
