import re
from urllib.parse import urlsplit
from pydantic import BaseModel, Field, field_validator, model_validator


def web_url(value: str) -> str:
    p = urlsplit(value)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or any(ord(c) < 32 for c in value):
        raise ValueError('Use a complete http:// or https:// URL without credentials.')
    return value


def asset_path(value: str) -> str:
    if value and not re.fullmatch(r'/uploads/[a-f0-9]{32}\.webp', value):
        raise ValueError('Choose an uploaded image.')
    return value


class Link(BaseModel):
    service: str = Field(default='custom', max_length=60)
    label: str = Field(min_length=1, max_length=100)
    url: str = Field(max_length=2048)
    icon: str = Field(default='music', max_length=80)
    icon_image: str = ''
    button_text: str = Field(default='Listen', max_length=30)
    enabled: bool = True
    featured: bool = False
    _url = field_validator('url')(web_url)
    _asset = field_validator('icon_image')(asset_path)

    @field_validator('icon')
    @classmethod
    def icon_name(cls, v):
        if not re.fullmatch(r'[a-z0-9-]+', v):
            raise ValueError('Invalid icon name.')
        return v


class Page(BaseModel):
    slug: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=160)
    artist: str = Field(default='', max_length=160)
    description: str = Field(default='The new single. Out now.\nChoose your streaming service.', max_length=3000)
    published: bool = False
    cover: str = ''
    background: str = ''
    background_x: int = Field(default=50, ge=0, le=100)
    background_y: int = Field(default=50, ge=0, le=100)
    background_color: str = '#09090b'
    panel_color: str = '#101014'
    accent: str = '#852b42'
    text_color: str = '#f1f0f3'
    accent_text: str = '#fff1f4'
    overlay: int = Field(default=25, ge=0, le=90)
    opacity: int = Field(default=60, ge=0, le=100)
    blur: int = Field(default=22, ge=0, le=50)
    links: list[Link] = Field(default_factory=list)
    meta_pixel: str = ''
    google_ads_id: str = ''
    google_ads_label: str = ''
    ga4_id: str = ''
    privacy_text: str = Field(default='', max_length=20000)
    imprint_text: str = Field(default='', max_length=20000)
    version: int = Field(default=0, ge=0)

    _assets = field_validator('cover', 'background')(asset_path)

    @field_validator('slug')
    @classmethod
    def clean_slug(cls, v):
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', v):
            raise ValueError('Use lowercase letters, digits and single hyphens for the URL stub.')
        return v

    @field_validator('background_color', 'panel_color', 'accent', 'text_color', 'accent_text')
    @classmethod
    def color(cls, v):
        if not re.fullmatch(r'#[a-fA-F0-9]{6}', v):
            raise ValueError('Use a six-digit hex color.')
        return v

    @field_validator('meta_pixel', 'google_ads_id', 'google_ads_label', 'ga4_id')
    @classmethod
    def tag_id(cls, v, info):
        patterns = {'meta_pixel': r'\d{5,30}', 'google_ads_id': r'AW-\d+',
                    'google_ads_label': r'[A-Za-z0-9_-]{1,100}', 'ga4_id': r'G-[A-Z0-9]+'}
        if v and not re.fullmatch(patterns[info.field_name], v):
            raise ValueError('Invalid tracking identifier.')
        return v

    @model_validator(mode='after')
    def tracking_pair(self):
        if bool(self.google_ads_id) != bool(self.google_ads_label):
            raise ValueError('Google Ads requires both a conversion ID and conversion label.')
        if self.published and any((self.meta_pixel, self.google_ads_id, self.ga4_id)) and not self.privacy_text.strip():
            raise ValueError('Add your privacy information before publishing with tracking enabled.')
        return self
