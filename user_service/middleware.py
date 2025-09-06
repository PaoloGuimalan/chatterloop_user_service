class ChatterloopMiddleware:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        # Example: Add a header to all responses or log request info
        def custom_start_response(status, headers, exc_info=None):
            # headers.append(("X-Hello", "World"))
            return start_response(status, headers, exc_info)

        # Call the original app with modified start_response
        return self.app(environ, custom_start_response)
