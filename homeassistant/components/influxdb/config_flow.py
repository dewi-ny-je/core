"""Config flow for InfluxDB integration."""

import logging
from pathlib import Path
import shutil
from typing import Any, override

import voluptuous as vol
from yarl import URL

from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import (
    CONF_DOMAIN,
    CONF_DOMAINS,
    CONF_ENTITIES,
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
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.entityfilter import CONF_ENTITY_GLOBS
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    FileSelector,
    FileSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    ObjectSelector,
    ObjectSelectorConfig,
    SelectOptionDict,
    Selector,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.storage import STORAGE_DIR

from . import DOMAIN, create_influx_url, get_influx_connection, options_from_config
from .const import (
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
    DEFAULT_BUCKET,
    DEFAULT_DATABASE,
    DEFAULT_HOST,
    DEFAULT_MEASUREMENT_ATTR,
    DEFAULT_PORT,
    DEFAULT_RETRY_COUNT,
    DEFAULT_VERIFY_SSL,
    MEASUREMENT_ATTRS,
    PRECISIONS,
)

_LOGGER = logging.getLogger(__name__)

MEASUREMENT_ATTR_LABELS = {
    "unit_of_measurement": "Unit of measurement",
    "domain__device_class": "Domain and device class",
    "entity_id": "Entity ID",
}
MEASUREMENT_ATTR_OPTIONS = [
    SelectOptionDict(value=attr, label=MEASUREMENT_ATTR_LABELS[attr])
    for attr in MEASUREMENT_ATTRS
]

CONF_ENTITY_GLOB = "entity_glob"
CONF_KEY = "key"
CONF_VALUE = "value"

FILTER_KEYS = {CONF_EXCLUDE, CONF_INCLUDE}
ATTRIBUTE_KEYS = {CONF_IGNORE_ATTRIBUTES, CONF_TAGS, CONF_TAGS_ATTRIBUTES}
MEASUREMENT_KEYS = {
    CONF_DEFAULT_MEASUREMENT,
    CONF_MEASUREMENT_ATTR,
    CONF_OVERRIDE_MEASUREMENT,
    CONF_PRECISION,
    CONF_RETRY_COUNT,
}
CUSTOMIZE_KEYS = {
    CONF_COMPONENT_CONFIG,
    CONF_COMPONENT_CONFIG_DOMAIN,
    CONF_COMPONENT_CONFIG_GLOB,
}

INFLUXDB_V1_SCHEMA = vol.Schema(
    {
        vol.Required(
            CONF_URL, default=f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"
        ): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.URL,
                autocomplete="url",
            ),
        ),
        vol.Required(CONF_VERIFY_SSL, default=False): bool,
        vol.Required(CONF_DB_NAME): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.TEXT,
            ),
        ),
        vol.Optional(CONF_USERNAME): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.TEXT,
                autocomplete="username",
            ),
        ),
        vol.Optional(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD,
                autocomplete="current-password",
            ),
        ),
        vol.Optional(CONF_SSL_CA_CERT): FileSelector(
            FileSelectorConfig(accept=".pem,.crt,.cer,.der")
        ),
    }
)

INFLUXDB_V2_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL, default="https://"): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.URL,
                autocomplete="url",
            ),
        ),
        vol.Required(CONF_VERIFY_SSL, default=False): bool,
        vol.Required(CONF_ORG): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.TEXT,
            ),
        ),
        vol.Required(CONF_BUCKET): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.TEXT,
            ),
        ),
        vol.Required(CONF_TOKEN): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD,
            ),
        ),
        vol.Optional(CONF_SSL_CA_CERT): FileSelector(
            FileSelectorConfig(accept=".pem,.crt,.cer,.der")
        ),
    }
)


async def _validate_influxdb_connection(
    hass: HomeAssistant, data: dict[str, Any]
) -> dict[str, str]:
    """Validate connection to influxdb."""

    def _test_connection() -> None:
        influx = get_influx_connection(data, test_write=True)
        influx.close()

    errors = {}

    try:
        await hass.async_add_executor_job(_test_connection)
    except ConnectionError as ex:
        _LOGGER.error(ex)
        if "SSLError" in ex.args[0]:
            errors = {"base": "ssl_error"}
        elif "database not found" in ex.args[0]:
            errors = {"base": "invalid_database"}
        elif "authorization failed" in ex.args[0]:
            errors = {"base": "invalid_auth"}
        elif "token" in ex.args[0]:
            errors = {"base": "invalid_config"}
        else:
            errors = {"base": "cannot_connect"}
    except Exception:
        _LOGGER.exception("Unknown error")
        errors = {"base": "unknown"}

    return errors


async def _save_uploaded_cert_file(hass: HomeAssistant, uploaded_file_id: str) -> Path:
    """Move the uploaded file to storage directory."""

    def _process_upload() -> Path:
        with process_uploaded_file(hass, uploaded_file_id) as file_path:
            dest_path = Path(hass.config.path(STORAGE_DIR, DOMAIN))
            dest_path.mkdir(exist_ok=True)
            file_name = f"influxdb{file_path.suffix}"
            dest_file = dest_path / file_name
            shutil.move(file_path, dest_file)
        return dest_file

    return await hass.async_add_executor_job(_process_upload)


def _domain_selector(hass: HomeAssistant, multiple: bool) -> SelectSelector:
    """Return a selector offering the domains currently known to Home Assistant."""
    return SelectSelector(
        SelectSelectorConfig(
            options=sorted({state.domain for state in hass.states.async_all()}),
            multiple=multiple,
            custom_value=True,
            sort=True,
        )
    )


def _attribute_selector(hass: HomeAssistant) -> SelectSelector:
    """Return a selector offering the attributes currently in use."""
    attributes: set[str] = set()
    for state in hass.states.async_all():
        attributes.update(state.attributes)
    return SelectSelector(
        SelectSelectorConfig(
            options=sorted(attributes),
            multiple=True,
            custom_value=True,
            sort=True,
        )
    )


def _filter_schema(hass: HomeAssistant) -> vol.Schema:
    """Return the schema of a single entity filter."""
    return vol.Schema(
        {
            vol.Optional(CONF_DOMAINS, default=list): _domain_selector(
                hass, multiple=True
            ),
            vol.Optional(CONF_ENTITIES, default=list): EntitySelector(
                EntitySelectorConfig(multiple=True)
            ),
            vol.Optional(CONF_ENTITY_GLOBS, default=list): TextSelector(
                TextSelectorConfig(multiple=True)
            ),
        }
    )


def _customize_selector(
    hass: HomeAssistant, key: str, key_selector: Selector
) -> ObjectSelector:
    """Return a selector for a list of per entity, glob or domain overrides."""
    return ObjectSelector(
        ObjectSelectorConfig(
            fields={
                key: {"selector": key_selector, "required": True},
                CONF_OVERRIDE_MEASUREMENT: {"selector": TextSelector()},
                CONF_IGNORE_ATTRIBUTES: {"selector": _attribute_selector(hass)},
            },
            multiple=True,
            label_field=key,
            translation_key="customize",
        )
    )


def _customize_to_rows(customize: dict[str, dict[str, Any]], key: str) -> list[dict]:
    """Convert stored overrides into rows for the object selector."""
    return [{key: pattern, **config} for pattern, config in customize.items()]


def _rows_to_customize(rows: list[dict[str, Any]], key: str) -> dict[str, dict]:
    """Convert rows from the object selector into stored overrides."""
    return {
        row[key]: {name: value for name, value in row.items() if name != key}
        for row in rows
    }


class InfluxDBConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for InfluxDB."""

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> InfluxDBOptionsFlow:
        """Get the options flow for this handler."""
        return InfluxDBOptionsFlow()

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step when user initializes an integration."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["configure_v1", "configure_v2"],
        )

    async def async_step_configure_v1(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step when user configures InfluxDB v1."""
        errors: dict[str, str] = {}

        if user_input is not None:
            url = URL(user_input[CONF_URL])
            data = {
                CONF_API_VERSION: DEFAULT_API_VERSION,
                CONF_HOST: url.host,
                CONF_PORT: url.port,
                CONF_USERNAME: user_input.get(CONF_USERNAME),
                CONF_PASSWORD: user_input.get(CONF_PASSWORD),
                CONF_DB_NAME: user_input[CONF_DB_NAME],
                CONF_SSL: url.scheme == "https",
                CONF_PATH: url.path,
                CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
            }
            if (cert := user_input.get(CONF_SSL_CA_CERT)) is not None:
                path = await _save_uploaded_cert_file(self.hass, cert)
                data[CONF_SSL_CA_CERT] = str(path)
            errors = await _validate_influxdb_connection(self.hass, data)

            if not errors:
                title = f"{data[CONF_DB_NAME]} ({data[CONF_HOST]})"
                return self.async_create_entry(title=title, data=data)

        schema = INFLUXDB_V1_SCHEMA

        return self.async_show_form(
            step_id="configure_v1",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_configure_v2(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step when user configures InfluxDB v2."""
        errors: dict[str, str] = {}

        if user_input is not None:
            data = {
                CONF_API_VERSION: API_VERSION_2,
                CONF_URL: user_input[CONF_URL],
                CONF_TOKEN: user_input[CONF_TOKEN],
                CONF_ORG: user_input[CONF_ORG],
                CONF_BUCKET: user_input[CONF_BUCKET],
                CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
            }
            if (cert := user_input.get(CONF_SSL_CA_CERT)) is not None:
                path = await _save_uploaded_cert_file(self.hass, cert)
                data[CONF_SSL_CA_CERT] = str(path)
            errors = await _validate_influxdb_connection(self.hass, data)

            if not errors:
                title = f"{data[CONF_BUCKET]} ({data[CONF_URL]})"
                return self.async_create_entry(title=title, data=data)

        schema = INFLUXDB_V2_SCHEMA

        return self.async_show_form(
            step_id="configure_v2",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration."""
        entry = self._get_reconfigure_entry()
        if entry.data[CONF_API_VERSION] == API_VERSION_2:
            return await self.async_step_reconfigure_v2(user_input)
        return await self.async_step_reconfigure_v1(user_input)

    async def async_step_reconfigure_v1(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of InfluxDB v1."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            url = URL(user_input[CONF_URL])
            data = {
                CONF_API_VERSION: DEFAULT_API_VERSION,
                CONF_HOST: url.host,
                CONF_PORT: url.port,
                CONF_USERNAME: user_input.get(CONF_USERNAME),
                CONF_PASSWORD: user_input.get(CONF_PASSWORD),
                CONF_DB_NAME: user_input[CONF_DB_NAME],
                CONF_SSL: url.scheme == "https",
                CONF_PATH: url.path,
                CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
            }
            if (cert := user_input.get(CONF_SSL_CA_CERT)) is not None:
                path = await _save_uploaded_cert_file(self.hass, cert)
                data[CONF_SSL_CA_CERT] = str(path)
            elif CONF_SSL_CA_CERT in entry.data:
                data[CONF_SSL_CA_CERT] = entry.data[CONF_SSL_CA_CERT]
            errors = await _validate_influxdb_connection(self.hass, data)

            if not errors:
                title = f"{data[CONF_DB_NAME]} ({data[CONF_HOST]})"
                return self.async_update_reload_and_abort(
                    entry, title=title, data_updates=data
                )

        suggested_values = dict(entry.data) | (user_input or {})
        if user_input is None:
            suggested_values[CONF_URL] = str(
                URL.build(
                    scheme="https" if entry.data.get(CONF_SSL) else "http",
                    host=entry.data.get(CONF_HOST, ""),
                    port=entry.data.get(CONF_PORT),
                    path=""
                    if entry.data.get(CONF_PATH) is None
                    else entry.data[CONF_PATH],
                )
            )

        return self.async_show_form(
            step_id="reconfigure_v1",
            data_schema=self.add_suggested_values_to_schema(
                INFLUXDB_V1_SCHEMA, suggested_values
            ),
            errors=errors,
        )

    async def async_step_reconfigure_v2(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of InfluxDB v2."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            data = {
                CONF_API_VERSION: API_VERSION_2,
                CONF_URL: user_input[CONF_URL],
                CONF_TOKEN: user_input[CONF_TOKEN],
                CONF_ORG: user_input[CONF_ORG],
                CONF_BUCKET: user_input[CONF_BUCKET],
                CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
            }
            if (cert := user_input.get(CONF_SSL_CA_CERT)) is not None:
                path = await _save_uploaded_cert_file(self.hass, cert)
                data[CONF_SSL_CA_CERT] = str(path)
            elif CONF_SSL_CA_CERT in entry.data:
                data[CONF_SSL_CA_CERT] = entry.data[CONF_SSL_CA_CERT]
            errors = await _validate_influxdb_connection(self.hass, data)

            if not errors:
                title = f"{data[CONF_BUCKET]} ({data[CONF_URL]})"
                return self.async_update_reload_and_abort(
                    entry, title=title, data_updates=data
                )

        return self.async_show_form(
            step_id="reconfigure_v2",
            data_schema=self.add_suggested_values_to_schema(
                INFLUXDB_V2_SCHEMA, entry.data | (user_input or {})
            ),
            errors=errors,
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle the initial step."""
        import_data = {**import_data}
        import_data.setdefault(CONF_API_VERSION, DEFAULT_API_VERSION)
        import_data.setdefault(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
        import_data.setdefault(CONF_DB_NAME, DEFAULT_DATABASE)
        import_data.setdefault(CONF_BUCKET, DEFAULT_BUCKET)

        api_version = import_data[CONF_API_VERSION]

        if api_version == DEFAULT_API_VERSION:
            host = import_data.get(CONF_HOST, DEFAULT_HOST)
            database = import_data[CONF_DB_NAME]
            title = f"{database} ({host})"
            data = {
                CONF_API_VERSION: api_version,
                CONF_HOST: host,
                CONF_PORT: import_data.get(CONF_PORT),
                CONF_USERNAME: import_data.get(CONF_USERNAME),
                CONF_PASSWORD: import_data.get(CONF_PASSWORD),
                CONF_DB_NAME: database,
                CONF_SSL: import_data.get(CONF_SSL),
                CONF_PATH: import_data.get(CONF_PATH),
                CONF_VERIFY_SSL: import_data[CONF_VERIFY_SSL],
                CONF_SSL_CA_CERT: import_data.get(CONF_SSL_CA_CERT),
            }
        else:
            create_influx_url(import_data)  # Only modifies dict for api_version == 2
            bucket = import_data[CONF_BUCKET]
            url = import_data.get(CONF_URL)
            title = f"{bucket} ({url})"
            data = {
                CONF_API_VERSION: api_version,
                CONF_URL: url,
                CONF_TOKEN: import_data.get(CONF_TOKEN),
                CONF_ORG: import_data.get(CONF_ORG),
                CONF_BUCKET: bucket,
                CONF_VERIFY_SSL: import_data[CONF_VERIFY_SSL],
                CONF_SSL_CA_CERT: import_data.get(CONF_SSL_CA_CERT),
            }

        errors = await _validate_influxdb_connection(self.hass, data)
        if errors:
            return self.async_abort(reason=errors["base"])

        return self.async_create_entry(
            title=title, data=data, options=options_from_config(import_data)
        )


class InfluxDBOptionsFlow(OptionsFlowWithReload):
    """Handle the InfluxDB options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick which group of options to change."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["filter", "attributes", "measurement", "customize"],
        )

    @callback
    def _async_save(
        self, replaced_keys: set[str], updates: dict[str, Any]
    ) -> ConfigFlowResult:
        """Store the options of a single step, dropping the ones cleared by the user."""
        options = {
            key: value
            for key, value in self.config_entry.options.items()
            if key not in replaced_keys
        }
        return self.async_create_entry(data=options | updates)

    async def async_step_filter(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure which entities are written to InfluxDB."""
        if user_input is not None:
            return self._async_save(
                FILTER_KEYS,
                {
                    CONF_INCLUDE: user_input[CONF_INCLUDE],
                    CONF_EXCLUDE: user_input[CONF_EXCLUDE],
                },
            )

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(CONF_INCLUDE): section(_filter_schema(self.hass)),
                vol.Required(CONF_EXCLUDE): section(_filter_schema(self.hass)),
            }
        )

        return self.async_show_form(
            step_id="filter",
            data_schema=self.add_suggested_values_to_schema(
                schema,
                {
                    CONF_INCLUDE: options.get(CONF_INCLUDE, {}),
                    CONF_EXCLUDE: options.get(CONF_EXCLUDE, {}),
                },
            ),
        )

    async def async_step_attributes(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure how entity attributes are written to InfluxDB."""
        if user_input is not None:
            return self._async_save(
                ATTRIBUTE_KEYS,
                {
                    CONF_IGNORE_ATTRIBUTES: user_input[CONF_IGNORE_ATTRIBUTES],
                    CONF_TAGS_ATTRIBUTES: user_input[CONF_TAGS_ATTRIBUTES],
                    CONF_TAGS: {
                        tag[CONF_KEY]: tag[CONF_VALUE] for tag in user_input[CONF_TAGS]
                    },
                },
            )

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(CONF_IGNORE_ATTRIBUTES, default=list): _attribute_selector(
                    self.hass
                ),
                vol.Optional(CONF_TAGS_ATTRIBUTES, default=list): _attribute_selector(
                    self.hass
                ),
                vol.Optional(CONF_TAGS, default=list): ObjectSelector(
                    ObjectSelectorConfig(
                        fields={
                            CONF_KEY: {"selector": TextSelector(), "required": True},
                            CONF_VALUE: {"selector": TextSelector(), "required": True},
                        },
                        multiple=True,
                        label_field=CONF_KEY,
                        description_field=CONF_VALUE,
                        translation_key="tags",
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="attributes",
            data_schema=self.add_suggested_values_to_schema(
                schema,
                {
                    CONF_IGNORE_ATTRIBUTES: options.get(CONF_IGNORE_ATTRIBUTES, []),
                    CONF_TAGS_ATTRIBUTES: options.get(CONF_TAGS_ATTRIBUTES, []),
                    CONF_TAGS: [
                        {CONF_KEY: key, CONF_VALUE: value}
                        for key, value in options.get(CONF_TAGS, {}).items()
                    ],
                },
            ),
        )

    async def async_step_measurement(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure the measurement names and write behavior."""
        if user_input is not None:
            return self._async_save(MEASUREMENT_KEYS, user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_MEASUREMENT_ATTR, default=DEFAULT_MEASUREMENT_ATTR
                ): SelectSelector(
                    SelectSelectorConfig(options=MEASUREMENT_ATTR_OPTIONS)
                ),
                vol.Optional(CONF_DEFAULT_MEASUREMENT): TextSelector(),
                vol.Optional(CONF_OVERRIDE_MEASUREMENT): TextSelector(),
                vol.Optional(CONF_PRECISION): SelectSelector(
                    SelectSelectorConfig(
                        options=PRECISIONS,
                        translation_key="precision",
                    )
                ),
                vol.Required(CONF_RETRY_COUNT, default=DEFAULT_RETRY_COUNT): vol.All(
                    NumberSelector(
                        NumberSelectorConfig(min=0, step=1, mode=NumberSelectorMode.BOX)
                    ),
                    vol.Coerce(int),
                ),
            }
        )

        return self.async_show_form(
            step_id="measurement",
            data_schema=self.add_suggested_values_to_schema(
                schema, self.config_entry.options
            ),
        )

    async def async_step_customize(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure the per entity, glob and domain overrides."""
        if user_input is not None:
            return self._async_save(
                CUSTOMIZE_KEYS,
                {
                    CONF_COMPONENT_CONFIG: _rows_to_customize(
                        user_input[CONF_COMPONENT_CONFIG], CONF_ENTITY_ID
                    ),
                    CONF_COMPONENT_CONFIG_GLOB: _rows_to_customize(
                        user_input[CONF_COMPONENT_CONFIG_GLOB], CONF_ENTITY_GLOB
                    ),
                    CONF_COMPONENT_CONFIG_DOMAIN: _rows_to_customize(
                        user_input[CONF_COMPONENT_CONFIG_DOMAIN], CONF_DOMAIN
                    ),
                },
            )

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(CONF_COMPONENT_CONFIG, default=list): _customize_selector(
                    self.hass, CONF_ENTITY_ID, EntitySelector()
                ),
                vol.Optional(
                    CONF_COMPONENT_CONFIG_GLOB, default=list
                ): _customize_selector(self.hass, CONF_ENTITY_GLOB, TextSelector()),
                vol.Optional(
                    CONF_COMPONENT_CONFIG_DOMAIN, default=list
                ): _customize_selector(
                    self.hass, CONF_DOMAIN, _domain_selector(self.hass, multiple=False)
                ),
            }
        )

        return self.async_show_form(
            step_id="customize",
            data_schema=self.add_suggested_values_to_schema(
                schema,
                {
                    CONF_COMPONENT_CONFIG: _customize_to_rows(
                        options.get(CONF_COMPONENT_CONFIG, {}), CONF_ENTITY_ID
                    ),
                    CONF_COMPONENT_CONFIG_GLOB: _customize_to_rows(
                        options.get(CONF_COMPONENT_CONFIG_GLOB, {}), CONF_ENTITY_GLOB
                    ),
                    CONF_COMPONENT_CONFIG_DOMAIN: _customize_to_rows(
                        options.get(CONF_COMPONENT_CONFIG_DOMAIN, {}), CONF_DOMAIN
                    ),
                },
            ),
        )
