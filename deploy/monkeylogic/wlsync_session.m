function id = wlsync_session(action, box, token_file, session_id)
%WLSYNC_SESSION  Open, close or read the sync box's session, from MATLAB.
%
%   ID = WLSYNC_SESSION('open',  BOX, TOKEN_FILE)
%   ID = WLSYNC_SESSION('close', BOX, TOKEN_FILE, SESSION_ID)
%   ID = WLSYNC_SESSION('close', BOX, TOKEN_FILE)
%   ID = WLSYNC_SESSION('show',  BOX)
%
%   Inputs
%     ACTION      'open', 'close' or 'show'. Anything else is an error.
%     BOX         host and port of the sync box, e.g. 'wl-sync-rig0.local:8740'.
%     TOKEN_FILE  path to a text file holding the session token. Needed for
%                 'open' and 'close'; 'show' needs no token.
%     SESSION_ID  'close' only: the id 'open' returned. Pass it. Given, the
%                 box is asked to close exactly that session and nothing is
%                 looked up, so a late close for this visit is refused
%                 (HTTP 409) instead of closing the next visit's session.
%                 Left out, 'close' closes whatever the box reports as open,
%                 which skips that guard. An empty SESSION_ID is an error,
%                 never a quiet fall back to the look-up.
%
%   Outputs
%     ID          char, e.g. '2026-10-18_02'.
%                 'open'  : the id of the session the box just opened.
%                 'close' : the id of the session the box just closed.
%                 'show'  : the open id, or '' when no session is open.
%
%   Example
%     id = wlsync_session('open', 'wl-sync-rig0.local:8740', 'C:\wl\session-token');
%     % ... run the visit, naming MonkeyLogic's data after ID ...
%     wlsync_session('close', 'wl-sync-rig0.local:8740', 'C:\wl\session-token', id);
%
%   Requires MATLAB R2016b or later (believed, UNVERIFIED): matlab.net.http,
%   jsonencode and isstring are believed to need it.
%
%   Every failure is an error, so a caller that does not catch it stops.
%   That is the point: a MonkeyLogic visit that starts without a session
%   would record entirely into unassigned/ on the box. Never type an id by
%   hand instead; the box mints every id and nothing else may.
%
%   Why it is written this way
%     * matlab.net.http.RequestMessage, not webwrite/webread. Those throw on
%       a non-2xx status without exposing the response body, so they cannot
%       show the box's own {"error": "..."} message.
%     * 'open' makes one request_id and reuses it on every retry. The box
%       answers a repeated request_id with the session it already opened,
%       so a retry after a lost reply cannot open a second session.
%       It retries (up to 3 more times, 1 s apart) only on a network
%       failure, a timeout, or HTTP 503 (the box's recording loop did not
%       take the request, and nothing changed). Any other status is final.
%     * Only a transport failure (believed to be matlab.net.http.HTTPException,
%       UNVERIFIED) is called "unreachable" and retried. Any other MATLAB
%       error stops at once as wlsync:internal, so a bug is not mistaken for
%       a network problem.
%     * ConnectTimeout is believed to bound connecting only (UNVERIFIED); a lost reply may hang rather
%       than time out. If so, stop MonkeyLogic and run 'show'.
%     * 'close' does not retry. A lost reply is better answered by 'show'
%       than by a second close, which would be refused with HTTP 409 if the
%       first one had in fact closed the session.
%     * The box needs a Content-Length on a POST and refuses a chunked
%       body (HTTP 400, "a valid Content-Length is required"). MATLAB is
%       believed to send a Content-Length for a body handed over whole, as
%       here (UNVERIFIED): a bench-day check.
%     * The token is read with strtrim(fileread(...)), and is never printed
%       or put in an error message.
%
%   NOT VERIFIED: nothing here has run under MATLAB, MonkeyLogic or against
%   the box. Items marked UNVERIFIED below are bench-day checks (see the
%   MonkeyLogic section of deploy/README.md).

    if nargin < 2 || ~(ischar(action) || isstring(action)) || ~(ischar(box) || isstring(box))
        error('wlsync:usage', 'usage: id = wlsync_session(action, box, token_file, session_id)');
    end
    action = char(action);
    box = char(box);
    base = ['http://' box];
    if nargin >= 4 && ~strcmp(action, 'close')
        % Only a close names a session. An id handed to 'open' would suggest the
        % caller can choose one, and the box mints every id.
        error('wlsync:usage', 'only ''close'' takes a session id; the box mints every id');
    end

    switch action
        case 'show'
            id = show_open(base, '');

        case 'open'
            if nargin < 3
                error('wlsync:usage', 'open needs a token file: wlsync_session(''open'', box, token_file)');
            end
            token = read_token(token_file);
            % One id for every attempt at this open: it is what makes the retry safe.
            request_id = char(java.util.UUID.randomUUID);
            body = struct('request_id', request_id);
            max_attempts = 4;   % the first try plus 3 retries
            for attempt = 1:max_attempts
                [status, data, failure] = send_request('POST', [base '/session/open'], token, body);
                retryable = ~isempty(failure) || status == 503;
                if ~retryable || attempt == max_attempts
                    break
                end
                pause(1);
            end
            if ~isempty(failure)
                error('wlsync:unreachable', ...
                    'sync box %s unreachable after %d attempts: %s', box, attempt, failure);
            end
            check_ok(status, data, 'open', box, token);
            id = as_char(get_field(data, 'session_id'));
            if isempty(id)
                error('wlsync:badreply', 'sync box %s answered 200 to open but gave no session_id', box);
            end
            closed = as_char(get_field(data, 'closed'));
            if ~isempty(closed)
                % The box had to close a session that was still open: a close was missed.
                warning('wlsync:autoclosed', ...
                    'sync box %s force-closed session %s while opening %s; a close was missed', ...
                    box, closed, id);
            end

        case 'close'
            if nargin < 3
                error('wlsync:usage', 'close needs a token file: wlsync_session(''close'', box, token_file, session_id)');
            end
            token = read_token(token_file);
            if nargin >= 4
                % Exactly the session the caller opened, never looked up: the box
                % refuses a close naming a session that is not the open one, and that
                % refusal is what stops a late close from ending the next visit.
                if ~(ischar(session_id) || isstring(session_id))
                    error('wlsync:usage', 'session_id must be text, such as the id ''open'' returned');
                end
                close_id = char(session_id);
                if isempty(close_id)
                    error('wlsync:usage', 'session_id is empty; pass the id ''open'' returned');
                end
            else
                % No id given: close whatever is open now. That skips the guard above,
                % since a late close then closes whatever has opened since.
                close_id = show_open(base, token);
                if isempty(close_id)
                    error('wlsync:nothingopen', 'nothing to close: sync box %s has no open session', box);
                end
            end
            [status, data, failure] = send_request('POST', [base '/session/close'], token, ...
                struct('session_id', close_id));
            if ~isempty(failure)
                error('wlsync:unreachable', 'sync box %s unreachable while closing %s: %s', ...
                    box, close_id, failure);
            end
            check_ok(status, data, 'close', box, token);
            id = as_char(get_field(data, 'closed'));

        otherwise
            error('wlsync:action', ...
                'unknown action ''%s''; the valid actions are ''open'', ''close'' and ''show''', action);
    end
end

% ---- helpers ------------------------------------------------------------------

function token = read_token(path)
    try
        token = strtrim(fileread(char(path)));
    catch
        % Name the path, never the contents.
        error('wlsync:token', 'cannot read the token file %s', char(path));
    end
    if isempty(token)
        error('wlsync:token', 'the token file %s is empty', char(path));
    end
end

function id = show_open(base, token)
    [status, data, failure] = send_request('GET', [base '/session'], '', []);
    if ~isempty(failure)
        error('wlsync:unreachable', 'sync box at %s unreachable: %s', base, failure);
    end
    check_ok(status, data, 'show', base, token);
    id = as_char(get_field(data, 'session_id'));
end

function [status, data, failure] = send_request(method, url, token, body)
% One HTTP exchange. A transport problem (no route, refused, timed out) comes
% back as FAILURE (a message); an HTTP answer of any status comes back as
% STATUS and DATA. Only the transport case is ever retried by the caller.
    status = 0; data = []; failure = '';
    try
        fields = matlab.net.http.HeaderField('Accept', 'application/json');
        if ~isempty(token)
            fields = [fields, matlab.net.http.HeaderField('Authorization', ['Bearer ' token])];
        end
        if isempty(body)
            req = matlab.net.http.RequestMessage(method, fields);
        else
            fields = [fields, matlab.net.http.HeaderField('Content-Type', 'application/json')];
            req = matlab.net.http.RequestMessage(method, fields, ...
                matlab.net.http.MessageBody(jsonencode(body)));
        end
        % UNVERIFIED: 'ConnectTimeout' as an HTTPOptions property name, and whether
        % it bounds the whole wait or only the connection. If MATLAB rejects the
        % name the call errors at once and is reported as unreachable; fix it on
        % bench day.
        opts = matlab.net.http.HTTPOptions('ConnectTimeout', 10);
        resp = send(req, matlab.net.URI(url), opts);
        status = double(resp.StatusCode);
        data = decode_body(resp);
    catch err
        % Only a transport failure is "unreachable" (and so retried by 'open').
        % UNVERIFIED: that send raises matlab.net.http.HTTPException for a refused
        % connection, unknown host or timeout. If it raises something else on a
        % real network failure, this will wrongly stop as internal; fix on bench day.
        if isa(err, 'matlab.net.http.HTTPException')
            failure = redact(err.message, token);
        else
            % A wrong option name, a HeaderField/MessageBody misuse, a bug: say so
            % at once, never retry, never call it a network problem.
            error('wlsync:internal', 'internal error (%s): %s', err.identifier, redact(err.message, token));
        end
    end
end

function text = redact(text, token)
% Server or MATLAB text goes into errors only after the token is removed.
    if ~isempty(token)
        text = strrep(text, token, '<token>');
    end
end

function data = decode_body(resp)
% The box answers every response, errors included, as JSON. MATLAB may already
% have decoded it (a struct) or may hand over text or bytes. UNVERIFIED: which
% of these resp.Body.Data actually is for this server; all three are handled.
    data = [];
    try
        raw = resp.Body.Data;
    catch
        return
    end
    if isstruct(raw)
        data = raw;
    elseif isa(raw, 'uint8')
        data = try_jsondecode(char(raw(:).'));
    elseif ischar(raw) || isstring(raw)
        data = try_jsondecode(char(raw));
    end
end

function data = try_jsondecode(text)
    try
        data = jsondecode(text);
    catch
        data = [];
    end
end

function check_ok(status, data, what, where_, token)
% Anything but 200 is an error carrying the box's own message and the status.
    if status == 200
        return
    end
    msg = redact(as_char(get_field(data, 'error')), token);
    if isempty(msg)
        msg = '(no message from the box)';
    end
    extra = '';
    if status == 409
        open_id = as_char(get_field(data, 'open'));
        if ~isempty(open_id)
            extra = sprintf(' [the open session is %s]', open_id);
        end
    end
    error('wlsync:http', 'sync box %s refused %s: HTTP %d: %s%s', where_, what, status, msg, extra);
end

function value = get_field(data, name)
    if isstruct(data) && isfield(data, name)
        value = data.(name);
    else
        value = [];
    end
end

function text = as_char(value)
% JSON null decodes to [] in MATLAB, so [] and a missing field both mean "none".
    if isempty(value)
        text = '';
    else
        text = char(value);
    end
end
