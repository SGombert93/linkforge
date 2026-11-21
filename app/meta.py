"""Send consented streaming-link clicks to the configured Meta dataset."""
import ipaddress
import logging
import time
from typing import Literal
from uuid import UUID

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class StreamingClick(BaseModel):
    event_id: UUID
    consent: Literal['accepted']
    release_id: str = Field(max_length=64)
    service: str = Field(max_length=60)
    link_index: int = Field(ge=0)


async def send_click(request: Request, click: StreamingClick, page: dict):
    settings = request.app.state.settings
    links = [link for link in page['links'] if link['enabled']]
    if (click.release_id != page['id'] or click.link_index >= len(links)
            or links[click.link_index]['service'] != click.service):
        raise HTTPException(409, 'Release has changed. Reload the page.')

    user_data = {'client_user_agent': request.headers.get('user-agent', '')[:1024]}
    if request.client:
        try:
            address = ipaddress.ip_address(request.client.host)
        except ValueError:
            address = None
        if address and address.is_global:
            user_data['client_ip_address'] = str(address)
    for field in ('fbp', 'fbc'):
        value = request.cookies.get('_' + field)
        if value and value.startswith('fb.') and len(value) <= 512:
            user_data[field] = value

    event = {
        'event_name': 'StreamingLinkClick', 'event_id': str(click.event_id),
        'event_time': int(time.time()), 'action_source': 'website',
        'event_source_url': f"{settings.base_url}/r/{page['slug']}",
        'custom_data': {'release_id': page['id'], 'release_slug': page['slug'],
                        'service': click.service, 'link_index': click.link_index},
        'user_data': user_data,
    }
    payload = {'data': [event]}
    if settings.meta_test_code:
        payload['test_event_code'] = settings.meta_test_code
    url = f'https://graph.facebook.com/v25.0/{settings.meta_dataset_id}/events'
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            response = await client.post(url, json=payload, headers={
                'Authorization': f'Bearer {settings.meta_access_token}',
            })
        response.raise_for_status()
        if response.json().get('events_received') != 1:
            raise ValueError('Unexpected event count')
    except (httpx.HTTPError, ValueError) as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        logger.warning('Meta click delivery failed (HTTP status %s)', status)
        raise HTTPException(502, 'Meta did not acknowledge the event.') from None
