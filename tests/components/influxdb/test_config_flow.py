"""Test the influxdb config flow."""

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from influxdb.exceptions import InfluxDBClientError, InfluxDBServerError
import pytest

from homeassistant import config_entries
from homeassistant.components.influxdb import (
    API_VERSION_2,
    CONF_API_VERSION,
    CONF_BUCKET,
    CONF_COMPONENT_CONFIG,
    CONF_COMPONENT_CONFIG_DOMAIN,
    CONF_COMPONENT_CONFIG_GLOB,
    CONF_DB_NAME,
    CONF_DEFAULT_MEASUREMENT,
    CONF_IGNORE_ATTRIBUTES,
    CONF_MEASUREMENT_ATTR,
    CONF_ORG,
    CONF_OVERRIDE_MEASUREMENT,
    CONF_PRECISION,
    CONF_RETRY_COUNT,
    CONF_SSL_CA_CERT,
    CONF_TAGS,
    CONF_TAGS_ATTRIBUTES,
    DEFAULT_API_VERSION,
    DOMAIN,
    ApiException,
)
from homeassistant.const import (
    CONF_DOMAIN,
    CONF_ENTITY_ID,
    CONF_EXCLUDE,
    CONF_HOST,
    CONF_INCLUDE,
    CONF_PASSWORD,
    CONF_PATH,
    CONF_PORT,
    CONF_SSL,
    CONF_TOKEN,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.entityfilter import (
    CONF_DOMAINS,
    CONF_ENTITIES,
    CONF_ENTITY_GLOBS,
)

from . import (
    BASE_V1_CONFIG,
    BASE_V2_CONFIG,
    INFLUX_CLIENT_PATH,
    _get_write_api_mock_v1,
    _get_write_api_mock_v2,
)

from tests.common import MockConfigEntry

PATH_FIXTURE = Path("/influxdb.crt")
FIXTURE_UPLOAD_UUID = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Override async_setup_entry."""
    with patch(
        "homeassistant.components.influxdb.async_setup_entry", return_value=True
    ) as mock_setup_entry:
        yield mock_setup_entry


@pytest.fixture(name="mock_client")
def mock_client_fixture(
    request: pytest.FixtureRequest,
) -> Generator[MagicMock]:
    """Patch the InfluxDBClient object with mock for version under test."""
    if request.param == API_VERSION_2:
        client_target = f"{INFLUX_CLIENT_PATH}V2"
    else:
        client_target = INFLUX_CLIENT_PATH

    with patch(client_target) as client:
        yield client


@contextmanager
def patch_file_upload(return_value=PATH_FIXTURE, side_effect=None):
    """Patch file upload. Yields the Path (return_value)."""
    with (
        patch(
            "homeassistant.components.influxdb.config_flow.process_uploaded_file"
        ) as file_upload_mock,
        patch("homeassistant.core_config.Config.path", return_value="/.storage"),
        patch(
            "pathlib.Path.mkdir",
        ) as mkdir_mock,
        patch(
            "shutil.move",
        ) as shutil_move_mock,
    ):
        file_upload_mock.return_value.__enter__.return_value = PATH_FIXTURE
        yield return_value
        if side_effect:
            mkdir_mock.assert_not_called()
            shutil_move_mock.assert_not_called()
        else:
            mkdir_mock.assert_called_once()
            shutil_move_mock.assert_called_once()


@pytest.mark.parametrize(
    ("mock_client", "config_base", "config_url", "get_write_api"),
    [
        (
            DEFAULT_API_VERSION,
            {
                CONF_URL: "http://localhost:8086",
                CONF_VERIFY_SSL: False,
                CONF_DB_NAME: "home_assistant",
                CONF_USERNAME: "user",
                CONF_PASSWORD: "pass",
            },
            {
                CONF_HOST: "localhost",
                CONF_PORT: 8086,
                CONF_SSL: False,
                CONF_PATH: "/",
            },
            _get_write_api_mock_v1,
        ),
        (
            DEFAULT_API_VERSION,
            {
                CONF_URL: "http://localhost:8086",
                CONF_VERIFY_SSL: False,
                CONF_DB_NAME: "home_assistant",
            },
            {
                CONF_HOST: "localhost",
                CONF_PORT: 8086,
                CONF_SSL: False,
                CONF_PATH: "/",
            },
            _get_write_api_mock_v1,
        ),
        (
            DEFAULT_API_VERSION,
            {
                CONF_URL: "https://influxdb.mydomain.com",
                CONF_VERIFY_SSL: True,
                CONF_DB_NAME: "home_assistant",
                CONF_USERNAME: "user",
                CONF_PASSWORD: "pass",
            },
            {
                CONF_HOST: "influxdb.mydomain.com",
                CONF_PORT: 443,
                CONF_SSL: True,
                CONF_PATH: "/",
            },
            _get_write_api_mock_v1,
        ),
    ],
    indirect=["mock_client"],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_setup_v1(
    hass: HomeAssistant,
    mock_client: MagicMock,
    config_base: dict[str, Any],
    config_url: dict[str, Any],
    get_write_api: Any,
) -> None:
    """Test we can setup an InfluxDB v1."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "configure_v1"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "configure_v1"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        config_base,
    )

    data = {
        CONF_API_VERSION: "1",
        CONF_HOST: config_url[CONF_HOST],
        CONF_PORT: config_url[CONF_PORT],
        CONF_USERNAME: config_base.get(CONF_USERNAME),
        CONF_PASSWORD: config_base.get(CONF_PASSWORD),
        CONF_DB_NAME: config_base[CONF_DB_NAME],
        CONF_SSL: config_url[CONF_SSL],
        CONF_PATH: config_url[CONF_PATH],
        CONF_VERIFY_SSL: config_base[CONF_VERIFY_SSL],
    }

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"{config_base['database']} ({config_url['host']})"
    assert result["data"] == data


@pytest.mark.parametrize(
    ("mock_client", "config_base", "config_url", "get_write_api"),
    [
        (
            DEFAULT_API_VERSION,
            {
                CONF_URL: "https://influxdb.mydomain.com",
                CONF_VERIFY_SSL: True,
                CONF_DB_NAME: "home_assistant",
                CONF_USERNAME: "user",
                CONF_PASSWORD: "pass",
                CONF_SSL_CA_CERT: FIXTURE_UPLOAD_UUID,
            },
            {
                CONF_HOST: "influxdb.mydomain.com",
                CONF_PORT: 443,
                CONF_SSL: True,
                CONF_PATH: "/",
            },
            _get_write_api_mock_v1,
        ),
    ],
    indirect=["mock_client"],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_setup_v1_ssl_cert(
    hass: HomeAssistant,
    mock_client: MagicMock,
    config_base: dict[str, Any],
    config_url: dict[str, Any],
    get_write_api: Any,
) -> None:
    """Test we can setup an InfluxDB v1 with SSL Certificate."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "configure_v1"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "configure_v1"
    assert result["errors"] == {}

    with (
        patch_file_upload(),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            config_base,
        )

    data = {
        CONF_API_VERSION: "1",
        CONF_HOST: config_url[CONF_HOST],
        CONF_PORT: config_url[CONF_PORT],
        CONF_USERNAME: config_base.get(CONF_USERNAME),
        CONF_PASSWORD: config_base.get(CONF_PASSWORD),
        CONF_DB_NAME: config_base[CONF_DB_NAME],
        CONF_SSL: config_url[CONF_SSL],
        CONF_PATH: config_url[CONF_PATH],
        CONF_VERIFY_SSL: config_base[CONF_VERIFY_SSL],
        CONF_SSL_CA_CERT: "/.storage/influxdb.crt",
    }

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"{config_base['database']} ({config_url['host']})"
    assert result["data"] == data


@pytest.mark.parametrize(
    ("mock_client", "config_base", "get_write_api"),
    [
        (
            API_VERSION_2,
            {
                CONF_URL: "http://localhost:8086",
                CONF_VERIFY_SSL: True,
                CONF_ORG: "my_org",
                CONF_BUCKET: "home_assistant",
                CONF_TOKEN: "token",
            },
            _get_write_api_mock_v2,
        ),
    ],
    indirect=["mock_client"],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_setup_v2(
    hass: HomeAssistant,
    mock_client: MagicMock,
    config_base: dict[str, Any],
    get_write_api: Any,
) -> None:
    """Test we can setup an InfluxDB v2."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "configure_v2"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "configure_v2"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        config_base,
    )

    data = {
        CONF_API_VERSION: "2",
        CONF_URL: config_base[CONF_URL],
        CONF_ORG: config_base[CONF_ORG],
        CONF_BUCKET: config_base.get(CONF_BUCKET),
        CONF_TOKEN: config_base.get(CONF_TOKEN),
        CONF_VERIFY_SSL: config_base[CONF_VERIFY_SSL],
    }

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"{config_base['bucket']} ({config_base['url']})"
    assert result["data"] == data


@pytest.mark.parametrize(
    ("mock_client", "config_base", "get_write_api"),
    [
        (
            API_VERSION_2,
            {
                CONF_URL: "http://localhost:8086",
                CONF_VERIFY_SSL: True,
                CONF_ORG: "my_org",
                CONF_BUCKET: "home_assistant",
                CONF_TOKEN: "token",
                CONF_SSL_CA_CERT: FIXTURE_UPLOAD_UUID,
            },
            _get_write_api_mock_v2,
        ),
    ],
    indirect=["mock_client"],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_setup_v2_ssl_cert(
    hass: HomeAssistant,
    mock_client: MagicMock,
    config_base: dict[str, Any],
    get_write_api: Any,
) -> None:
    """Test we can setup an InfluxDB v2 with SSL Certificate."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "configure_v2"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "configure_v2"
    assert result["errors"] == {}

    with (
        patch_file_upload(),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            config_base,
        )

    data = {
        CONF_API_VERSION: "2",
        CONF_URL: config_base[CONF_URL],
        CONF_ORG: config_base[CONF_ORG],
        CONF_BUCKET: config_base.get(CONF_BUCKET),
        CONF_TOKEN: config_base.get(CONF_TOKEN),
        CONF_VERIFY_SSL: config_base[CONF_VERIFY_SSL],
        CONF_SSL_CA_CERT: "/.storage/influxdb.crt",
    }

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"{config_base['bucket']} ({config_base['url']})"
    assert result["data"] == data


@pytest.mark.parametrize(
    (
        "mock_client",
        "config_base",
        "api_version",
        "get_write_api",
        "test_exception",
        "reason",
    ),
    [
        (
            DEFAULT_API_VERSION,
            {
                CONF_URL: "http://localhost:8086",
                CONF_VERIFY_SSL: False,
                CONF_DB_NAME: "home_assistant",
                CONF_USERNAME: "user",
                CONF_PASSWORD: "pass",
            },
            DEFAULT_API_VERSION,
            _get_write_api_mock_v1,
            InfluxDBClientError("SSLError"),
            "ssl_error",
        ),
        (
            DEFAULT_API_VERSION,
            {
                CONF_URL: "http://localhost:8086",
                CONF_VERIFY_SSL: False,
                CONF_DB_NAME: "home_assistant",
                CONF_USERNAME: "user",
                CONF_PASSWORD: "pass",
            },
            DEFAULT_API_VERSION,
            _get_write_api_mock_v1,
            InfluxDBClientError("database not found"),
            "invalid_database",
        ),
        (
            DEFAULT_API_VERSION,
            {
                CONF_URL: "http://localhost:8086",
                CONF_VERIFY_SSL: False,
                CONF_DB_NAME: "home_assistant",
                CONF_USERNAME: "user",
                CONF_PASSWORD: "pass",
            },
            DEFAULT_API_VERSION,
            _get_write_api_mock_v1,
            InfluxDBClientError("authorization failed"),
            "invalid_auth",
        ),
        (
            API_VERSION_2,
            {
                CONF_URL: "http://localhost:8086",
                CONF_VERIFY_SSL: True,
                CONF_ORG: "my_org",
                CONF_BUCKET: "home_assistant",
                CONF_TOKEN: "token",
            },
            API_VERSION_2,
            _get_write_api_mock_v2,
            ApiException("SSLError"),
            "ssl_error",
        ),
        (
            API_VERSION_2,
            {
                CONF_URL: "http://localhost:8086",
                CONF_VERIFY_SSL: True,
                CONF_ORG: "my_org",
                CONF_BUCKET: "home_assistant",
                CONF_TOKEN: "token",
            },
            API_VERSION_2,
            _get_write_api_mock_v2,
            ApiException("token"),
            "invalid_config",
        ),
        (
            DEFAULT_API_VERSION,
            {
                CONF_URL: "http://localhost:8086",
                CONF_VERIFY_SSL: False,
                CONF_DB_NAME: "home_assistant",
                CONF_USERNAME: "user",
                CONF_PASSWORD: "pass",
            },
            DEFAULT_API_VERSION,
            _get_write_api_mock_v1,
            InfluxDBClientError("some other error"),
            "cannot_connect",
        ),
        (
            DEFAULT_API_VERSION,
            {
                CONF_URL: "http://localhost:8086",
                CONF_VERIFY_SSL: False,
                CONF_DB_NAME: "home_assistant",
                CONF_USERNAME: "user",
                CONF_PASSWORD: "pass",
            },
            DEFAULT_API_VERSION,
            _get_write_api_mock_v1,
            Exception("unexpected"),
            "unknown",
        ),
    ],
    indirect=["mock_client"],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_setup_connection_error(
    hass: HomeAssistant,
    mock_client: MagicMock,
    config_base: dict[str, Any],
    api_version: str,
    get_write_api: Any,
    test_exception: Exception,
    reason: str,
) -> None:
    """Test connection error during setup of InfluxDB v2."""
    write_api = get_write_api(mock_client)
    write_api.side_effect = test_exception

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": f"configure_v{api_version}"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == f"configure_v{api_version}"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        config_base,
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": reason}

    write_api.side_effect = None

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        config_base,
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    ("mock_client", "config_base", "get_write_api"),
    [
        (
            DEFAULT_API_VERSION,
            BASE_V1_CONFIG,
            _get_write_api_mock_v1,
        ),
    ],
    indirect=["mock_client"],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_single_instance(
    hass: HomeAssistant,
    mock_client: MagicMock,
    config_base: dict[str, Any],
    get_write_api: Any,
) -> None:
    """Test we cannot setup a second entry for InfluxDB."""
    mock_entry = MockConfigEntry(
        domain=DOMAIN,
        data=config_base,
    )

    mock_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


@pytest.mark.parametrize(
    ("mock_client", "config_base", "get_write_api", "db_name", "host"),
    [
        (
            DEFAULT_API_VERSION,
            BASE_V1_CONFIG,
            _get_write_api_mock_v1,
            BASE_V1_CONFIG[CONF_DB_NAME],
            BASE_V1_CONFIG[CONF_HOST],
        ),
        (
            API_VERSION_2,
            BASE_V2_CONFIG,
            _get_write_api_mock_v2,
            BASE_V2_CONFIG[CONF_BUCKET],
            BASE_V2_CONFIG[CONF_URL],
        ),
    ],
    indirect=["mock_client"],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_import(
    hass: HomeAssistant,
    mock_client: MagicMock,
    config_base: dict[str, Any],
    get_write_api: Any,
    db_name: str,
    host: str,
) -> None:
    """Test we can import."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_IMPORT},
        data=config_base,
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"{db_name} ({host})"
    assert result["data"] == config_base

    assert get_write_api(mock_client).call_count == 1


@pytest.mark.parametrize(
    ("mock_client", "config_base", "get_write_api", "test_exception", "reason"),
    [
        (
            DEFAULT_API_VERSION,
            BASE_V1_CONFIG,
            _get_write_api_mock_v1,
            ConnectionError("fail"),
            "cannot_connect",
        ),
        (
            DEFAULT_API_VERSION,
            BASE_V1_CONFIG,
            _get_write_api_mock_v1,
            InfluxDBClientError("fail"),
            "cannot_connect",
        ),
        (
            DEFAULT_API_VERSION,
            BASE_V1_CONFIG,
            _get_write_api_mock_v1,
            InfluxDBServerError("fail"),
            "cannot_connect",
        ),
        (
            API_VERSION_2,
            BASE_V2_CONFIG,
            _get_write_api_mock_v2,
            ConnectionError("fail"),
            "cannot_connect",
        ),
        (
            API_VERSION_2,
            BASE_V2_CONFIG,
            _get_write_api_mock_v2,
            ApiException(http_resp=MagicMock()),
            "invalid_config",
        ),
        (
            API_VERSION_2,
            BASE_V2_CONFIG,
            _get_write_api_mock_v2,
            Exception(),
            "unknown",
        ),
    ],
    indirect=["mock_client"],
)
async def test_import_connection_error(
    hass: HomeAssistant,
    mock_client: MagicMock,
    config_base: dict[str, Any],
    get_write_api: Any,
    test_exception: Exception,
    reason: str,
) -> None:
    """Test abort on connection error."""
    write_api = get_write_api(mock_client)
    write_api.side_effect = test_exception

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_IMPORT},
        data=config_base,
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason


@pytest.mark.parametrize(
    ("mock_client", "config_base", "get_write_api"),
    [
        (
            DEFAULT_API_VERSION,
            BASE_V1_CONFIG,
            _get_write_api_mock_v1,
        ),
    ],
    indirect=["mock_client"],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_single_instance_import(
    hass: HomeAssistant,
    mock_client: MagicMock,
    config_base: dict[str, Any],
    get_write_api: Any,
) -> None:
    """Test we cannot setup a second entry for InfluxDB."""
    mock_entry = MockConfigEntry(
        domain=DOMAIN,
        data=config_base,
    )

    mock_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_IMPORT},
        data=config_base,
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


@pytest.mark.parametrize(
    ("mock_client", "entry_data", "user_input", "expected_data", "expected_title"),
    [
        (
            DEFAULT_API_VERSION,
            {
                CONF_API_VERSION: DEFAULT_API_VERSION,
                CONF_HOST: "localhost",
                CONF_PORT: 8086,
                CONF_USERNAME: "user",
                CONF_PASSWORD: "pass",
                CONF_DB_NAME: "home_assistant",
                CONF_SSL: False,
                CONF_PATH: "/",
                CONF_VERIFY_SSL: False,
            },
            {
                CONF_URL: "https://newhost:9999",
                CONF_VERIFY_SSL: True,
                CONF_DB_NAME: "new_db",
                CONF_USERNAME: "new_user",
                CONF_PASSWORD: "new_pass",
            },
            {
                CONF_API_VERSION: DEFAULT_API_VERSION,
                CONF_HOST: "newhost",
                CONF_PORT: 9999,
                CONF_USERNAME: "new_user",
                CONF_PASSWORD: "new_pass",
                CONF_DB_NAME: "new_db",
                CONF_SSL: True,
                CONF_PATH: "/",
                CONF_VERIFY_SSL: True,
            },
            "new_db (newhost)",
        ),
        (
            DEFAULT_API_VERSION,
            {
                CONF_API_VERSION: DEFAULT_API_VERSION,
                CONF_HOST: "localhost",
                CONF_PORT: 8086,
                CONF_USERNAME: "user",
                CONF_PASSWORD: "pass",
                CONF_DB_NAME: "home_assistant",
                CONF_SSL: False,
                CONF_PATH: None,
                CONF_VERIFY_SSL: False,
            },
            {
                CONF_URL: "https://newhost:9999",
                CONF_VERIFY_SSL: True,
                CONF_DB_NAME: "new_db",
                CONF_USERNAME: "new_user",
                CONF_PASSWORD: "new_pass",
            },
            {
                CONF_API_VERSION: DEFAULT_API_VERSION,
                CONF_HOST: "newhost",
                CONF_PORT: 9999,
                CONF_USERNAME: "new_user",
                CONF_PASSWORD: "new_pass",
                CONF_DB_NAME: "new_db",
                CONF_SSL: True,
                CONF_PATH: "/",
                CONF_VERIFY_SSL: True,
            },
            "new_db (newhost)",
        ),
    ],
    indirect=["mock_client"],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_reconfigure_v1(
    hass: HomeAssistant,
    mock_client: MagicMock,
    entry_data: dict[str, Any],
    user_input: dict[str, Any],
    expected_data: dict[str, Any],
    expected_title: str,
) -> None:
    """Test reconfiguration of InfluxDB v1."""
    mock_entry = MockConfigEntry(
        domain=DOMAIN,
        data=entry_data,
    )
    mock_entry.add_to_hass(hass)

    result = await mock_entry.start_reconfigure_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure_v1"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input,
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_entry.data == expected_data
    assert mock_entry.title == expected_title


@pytest.mark.parametrize(
    ("mock_client", "entry_data", "user_input", "expected_data", "expected_title"),
    [
        (
            API_VERSION_2,
            {
                CONF_API_VERSION: API_VERSION_2,
                CONF_URL: "http://localhost:8086",
                CONF_TOKEN: "old_token",
                CONF_ORG: "old_org",
                CONF_BUCKET: "old_bucket",
                CONF_VERIFY_SSL: False,
            },
            {
                CONF_URL: "https://newhost:9999",
                CONF_VERIFY_SSL: True,
                CONF_ORG: "new_org",
                CONF_BUCKET: "new_bucket",
                CONF_TOKEN: "new_token",
            },
            {
                CONF_API_VERSION: API_VERSION_2,
                CONF_URL: "https://newhost:9999",
                CONF_TOKEN: "new_token",
                CONF_ORG: "new_org",
                CONF_BUCKET: "new_bucket",
                CONF_VERIFY_SSL: True,
            },
            "new_bucket (https://newhost:9999)",
        ),
    ],
    indirect=["mock_client"],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_reconfigure_v2(
    hass: HomeAssistant,
    mock_client: MagicMock,
    entry_data: dict[str, Any],
    user_input: dict[str, Any],
    expected_data: dict[str, Any],
    expected_title: str,
) -> None:
    """Test reconfiguration of InfluxDB v2."""
    mock_entry = MockConfigEntry(
        domain=DOMAIN,
        data=entry_data,
    )
    mock_entry.add_to_hass(hass)

    result = await mock_entry.start_reconfigure_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure_v2"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input,
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_entry.data == expected_data
    assert mock_entry.title == expected_title


@pytest.mark.parametrize(
    ("mock_client", "entry_data", "user_input"),
    [
        (
            DEFAULT_API_VERSION,
            {
                CONF_API_VERSION: DEFAULT_API_VERSION,
                CONF_HOST: "localhost",
                CONF_PORT: 8086,
                CONF_USERNAME: "user",
                CONF_PASSWORD: "pass",
                CONF_DB_NAME: "home_assistant",
                CONF_SSL: True,
                CONF_PATH: "/",
                CONF_VERIFY_SSL: True,
                CONF_SSL_CA_CERT: "/old/cert.pem",
            },
            {
                CONF_URL: "https://localhost:8086",
                CONF_VERIFY_SSL: True,
                CONF_DB_NAME: "home_assistant",
                CONF_USERNAME: "user",
                CONF_PASSWORD: "pass",
                CONF_SSL_CA_CERT: FIXTURE_UPLOAD_UUID,
            },
        ),
    ],
    indirect=["mock_client"],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_reconfigure_v1_ssl_cert(
    hass: HomeAssistant,
    mock_client: MagicMock,
    entry_data: dict[str, Any],
    user_input: dict[str, Any],
) -> None:
    """Test reconfiguration of InfluxDB v1 with SSL certificate upload."""
    mock_entry = MockConfigEntry(
        domain=DOMAIN,
        data=entry_data,
    )
    mock_entry.add_to_hass(hass)

    result = await mock_entry.start_reconfigure_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure_v1"

    with patch_file_upload():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input,
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_entry.data[CONF_SSL_CA_CERT] == "/.storage/influxdb.crt"


@pytest.mark.parametrize(
    ("mock_client", "entry_data", "user_input"),
    [
        (
            API_VERSION_2,
            {
                CONF_API_VERSION: API_VERSION_2,
                CONF_URL: "https://localhost:8086",
                CONF_TOKEN: "token",
                CONF_ORG: "org",
                CONF_BUCKET: "bucket",
                CONF_VERIFY_SSL: True,
                CONF_SSL_CA_CERT: "/old/cert.pem",
            },
            {
                CONF_URL: "https://localhost:8086",
                CONF_VERIFY_SSL: True,
                CONF_ORG: "org",
                CONF_BUCKET: "bucket",
                CONF_TOKEN: "token",
                CONF_SSL_CA_CERT: FIXTURE_UPLOAD_UUID,
            },
        ),
    ],
    indirect=["mock_client"],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_reconfigure_v2_ssl_cert(
    hass: HomeAssistant,
    mock_client: MagicMock,
    entry_data: dict[str, Any],
    user_input: dict[str, Any],
) -> None:
    """Test reconfiguration of InfluxDB v2 with SSL certificate upload."""
    mock_entry = MockConfigEntry(
        domain=DOMAIN,
        data=entry_data,
    )
    mock_entry.add_to_hass(hass)

    result = await mock_entry.start_reconfigure_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure_v2"

    with patch_file_upload():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input,
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_entry.data[CONF_SSL_CA_CERT] == "/.storage/influxdb.crt"


@pytest.mark.parametrize(
    ("mock_client", "entry_data", "user_input"),
    [
        (
            DEFAULT_API_VERSION,
            {
                CONF_API_VERSION: DEFAULT_API_VERSION,
                CONF_HOST: "localhost",
                CONF_PORT: 8086,
                CONF_USERNAME: "user",
                CONF_PASSWORD: "pass",
                CONF_DB_NAME: "home_assistant",
                CONF_SSL: False,
                CONF_PATH: "/",
                CONF_VERIFY_SSL: False,
                CONF_SSL_CA_CERT: "/old/cert.pem",
            },
            {
                CONF_URL: "http://localhost:8086",
                CONF_VERIFY_SSL: False,
                CONF_DB_NAME: "home_assistant",
                CONF_USERNAME: "user",
                CONF_PASSWORD: "pass",
            },
        ),
        (
            API_VERSION_2,
            {
                CONF_API_VERSION: API_VERSION_2,
                CONF_URL: "https://localhost:8086",
                CONF_TOKEN: "token",
                CONF_ORG: "org",
                CONF_BUCKET: "bucket",
                CONF_VERIFY_SSL: True,
                CONF_SSL_CA_CERT: "/old/cert.pem",
            },
            {
                CONF_URL: "https://localhost:8086",
                CONF_VERIFY_SSL: True,
                CONF_ORG: "org",
                CONF_BUCKET: "bucket",
                CONF_TOKEN: "token",
            },
        ),
    ],
    indirect=["mock_client"],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_reconfigure_preserves_existing_cert(
    hass: HomeAssistant,
    mock_client: MagicMock,
    entry_data: dict[str, Any],
    user_input: dict[str, Any],
) -> None:
    """Test reconfiguration preserves existing cert when none uploaded."""
    mock_entry = MockConfigEntry(
        domain=DOMAIN,
        data=entry_data,
    )
    mock_entry.add_to_hass(hass)

    result = await mock_entry.start_reconfigure_flow(hass)

    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input,
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_entry.data[CONF_SSL_CA_CERT] == "/old/cert.pem"


@pytest.mark.parametrize(
    (
        "mock_client",
        "entry_data",
        "user_input",
        "get_write_api",
        "test_exception",
        "reason",
    ),
    [
        (
            DEFAULT_API_VERSION,
            {
                CONF_API_VERSION: DEFAULT_API_VERSION,
                CONF_HOST: "localhost",
                CONF_PORT: 8086,
                CONF_DB_NAME: "home_assistant",
                CONF_SSL: False,
                CONF_PATH: "/",
                CONF_VERIFY_SSL: False,
            },
            {
                CONF_URL: "http://localhost:8086",
                CONF_VERIFY_SSL: False,
                CONF_DB_NAME: "home_assistant",
            },
            _get_write_api_mock_v1,
            InfluxDBClientError("SSLError"),
            "ssl_error",
        ),
        (
            DEFAULT_API_VERSION,
            {
                CONF_API_VERSION: DEFAULT_API_VERSION,
                CONF_HOST: "localhost",
                CONF_PORT: 8086,
                CONF_DB_NAME: "home_assistant",
                CONF_SSL: False,
                CONF_PATH: "/",
                CONF_VERIFY_SSL: False,
            },
            {
                CONF_URL: "http://localhost:8086",
                CONF_VERIFY_SSL: False,
                CONF_DB_NAME: "home_assistant",
            },
            _get_write_api_mock_v1,
            InfluxDBClientError("authorization failed"),
            "invalid_auth",
        ),
        (
            API_VERSION_2,
            {
                CONF_API_VERSION: API_VERSION_2,
                CONF_URL: "http://localhost:8086",
                CONF_TOKEN: "token",
                CONF_ORG: "org",
                CONF_BUCKET: "bucket",
                CONF_VERIFY_SSL: False,
            },
            {
                CONF_URL: "http://localhost:8086",
                CONF_VERIFY_SSL: False,
                CONF_ORG: "org",
                CONF_BUCKET: "bucket",
                CONF_TOKEN: "token",
            },
            _get_write_api_mock_v2,
            ApiException("SSLError"),
            "ssl_error",
        ),
    ],
    indirect=["mock_client"],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_reconfigure_connection_error(
    hass: HomeAssistant,
    mock_client: MagicMock,
    entry_data: dict[str, Any],
    user_input: dict[str, Any],
    get_write_api: Any,
    test_exception: Exception,
    reason: str,
) -> None:
    """Test reconfiguration handles connection errors."""
    mock_entry = MockConfigEntry(
        domain=DOMAIN,
        data=entry_data,
    )
    mock_entry.add_to_hass(hass)

    write_api = get_write_api(mock_client)
    write_api.side_effect = test_exception

    result = await mock_entry.start_reconfigure_flow(hass)

    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input,
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": reason}

    write_api.side_effect = None

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input,
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"


@pytest.fixture(name="options_entry")
async def options_entry_fixture(hass: HomeAssistant) -> MockConfigEntry:
    """Return a set up config entry to run the options flow against."""
    # The domain and attribute selectors are built from the existing states
    hass.states.async_set("sensor.kitchen", "1", {"unit_of_measurement": "W"})

    mock_entry = MockConfigEntry(domain=DOMAIN, data=BASE_V1_CONFIG)
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    return mock_entry


@pytest.mark.parametrize(
    ("step", "user_input", "expected_options"),
    [
        pytest.param(
            "filter",
            {
                CONF_INCLUDE: {
                    CONF_DOMAINS: ["sensor"],
                    CONF_ENTITIES: ["light.kitchen"],
                    CONF_ENTITY_GLOBS: ["binary_sensor.*"],
                },
                CONF_EXCLUDE: {
                    CONF_DOMAINS: ["climate"],
                    CONF_ENTITIES: ["sensor.secret"],
                    CONF_ENTITY_GLOBS: ["sensor.private_*"],
                },
            },
            {
                CONF_INCLUDE: {
                    CONF_DOMAINS: ["sensor"],
                    CONF_ENTITIES: ["light.kitchen"],
                    CONF_ENTITY_GLOBS: ["binary_sensor.*"],
                },
                CONF_EXCLUDE: {
                    CONF_DOMAINS: ["climate"],
                    CONF_ENTITIES: ["sensor.secret"],
                    CONF_ENTITY_GLOBS: ["sensor.private_*"],
                },
            },
            id="filter",
        ),
        pytest.param(
            "attributes",
            {
                CONF_IGNORE_ATTRIBUTES: ["icon", "friendly_name"],
                CONF_TAGS_ATTRIBUTES: ["device_class"],
                CONF_TAGS: [{"key": "instance", "value": "production"}],
            },
            {
                CONF_IGNORE_ATTRIBUTES: ["icon", "friendly_name"],
                CONF_TAGS_ATTRIBUTES: ["device_class"],
                CONF_TAGS: {"instance": "production"},
            },
            id="attributes",
        ),
        pytest.param(
            "measurement",
            {
                CONF_MEASUREMENT_ATTR: "entity_id",
                CONF_DEFAULT_MEASUREMENT: "state",
                CONF_OVERRIDE_MEASUREMENT: "home_assistant",
                CONF_PRECISION: "ms",
                CONF_RETRY_COUNT: 3,
            },
            {
                CONF_MEASUREMENT_ATTR: "entity_id",
                CONF_DEFAULT_MEASUREMENT: "state",
                CONF_OVERRIDE_MEASUREMENT: "home_assistant",
                CONF_PRECISION: "ms",
                CONF_RETRY_COUNT: 3,
            },
            id="measurement",
        ),
        pytest.param(
            "customize",
            {
                CONF_COMPONENT_CONFIG: [
                    {
                        CONF_ENTITY_ID: "sensor.kitchen",
                        CONF_OVERRIDE_MEASUREMENT: "kitchen",
                    }
                ],
                CONF_COMPONENT_CONFIG_GLOB: [
                    {
                        "entity_glob": "sensor.weather_*",
                        CONF_IGNORE_ATTRIBUTES: ["icon"],
                    }
                ],
                CONF_COMPONENT_CONFIG_DOMAIN: [
                    {CONF_DOMAIN: "climate", CONF_OVERRIDE_MEASUREMENT: "hvac"}
                ],
            },
            {
                CONF_COMPONENT_CONFIG: {
                    "sensor.kitchen": {CONF_OVERRIDE_MEASUREMENT: "kitchen"}
                },
                CONF_COMPONENT_CONFIG_GLOB: {
                    "sensor.weather_*": {CONF_IGNORE_ATTRIBUTES: ["icon"]}
                },
                CONF_COMPONENT_CONFIG_DOMAIN: {
                    "climate": {CONF_OVERRIDE_MEASUREMENT: "hvac"}
                },
            },
            id="customize",
        ),
    ],
)
@pytest.mark.parametrize("mock_client", [DEFAULT_API_VERSION], indirect=True)
@pytest.mark.usefixtures("mock_client")
async def test_options_flow(
    hass: HomeAssistant,
    options_entry: MockConfigEntry,
    step: str,
    user_input: dict[str, Any],
    expected_options: dict[str, Any],
) -> None:
    """Test each step of the options flow stores its options."""
    result = await hass.config_entries.options.async_init(options_entry.entry_id)

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == step

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert options_entry.options == expected_options


@pytest.mark.parametrize("mock_client", [DEFAULT_API_VERSION], indirect=True)
@pytest.mark.usefixtures("mock_client")
async def test_options_flow_only_replaces_own_options(
    hass: HomeAssistant,
    options_entry: MockConfigEntry,
) -> None:
    """Test a step keeps the options of the other steps and drops cleared values."""
    hass.config_entries.async_update_entry(
        options_entry,
        options={
            CONF_DEFAULT_MEASUREMENT: "state",
            CONF_RETRY_COUNT: 3,
            CONF_TAGS: {"instance": "production"},
        },
    )
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(options_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "measurement"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_MEASUREMENT_ATTR: "entity_id", CONF_RETRY_COUNT: 0},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert options_entry.options == {
        CONF_MEASUREMENT_ATTR: "entity_id",
        CONF_RETRY_COUNT: 0,
        CONF_TAGS: {"instance": "production"},
    }
