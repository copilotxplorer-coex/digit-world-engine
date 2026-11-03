import errno
import functools
import logging

def is_client_disconnect(exc):
    codes={errno.EPIPE,errno.ECONNRESET,errno.ECONNABORTED,10053,10054,10058}
    return isinstance(exc,(BrokenPipeError,ConnectionResetError,ConnectionAbortedError)) or (isinstance(exc,OSError) and (exc.errno in codes or getattr(exc,'winerror',None) in codes))

def mark_disconnected(handler):
    handler.close_connection=True
    logging.getLogger('digit.http').debug('client_disconnected')

def disconnect_guard(method):
    @functools.wraps(method)
    def guarded(self,*args,**kwargs):
        try:return method(self,*args,**kwargs)
        except OSError as exc:
            if not is_client_disconnect(exc):raise
            mark_disconnected(self)
    return guarded
