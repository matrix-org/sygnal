# Copyright 2025 New Vector Ltd.
# Copyright 2015 OpenMarket Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.
from typing import Optional

from twisted.internet.error import ConnectError


class InvalidNotificationException(Exception):
    pass


class PushkinSetupException(Exception):
    pass


class NotificationDispatchException(Exception):
    pass


class TemporaryNotificationDispatchException(Exception):
    """
    To be used by pushkins for errors that are not our fault and are
    hopefully temporary, so the request should possibly be retried soon.
    """

    def __init__(self, *args: object, custom_retry_delay: Optional[int] = None) -> None:
        super().__init__(*args)
        self.custom_retry_delay = custom_retry_delay


class NotificationQuotaDispatchException(Exception):
    """
    To be used by pushkins for errors that are do to exceeding the quota
    limits and are hopefully temporary, so the request should possibly be
    retried soon.
    """

    def __init__(self, *args: object, custom_retry_delay: Optional[int] = None) -> None:
        super().__init__(*args)
        self.custom_retry_delay = custom_retry_delay


class ProxyConnectError(ConnectError):
    """
    Exception raised when we are unable to start a connection using a HTTP proxy
    This indicates an issue with the HTTP Proxy in use rather than the final
    endpoint we wanted to contact.
    """

    pass
