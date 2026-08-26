-- Esquema legacy mínimo para probar los scripts de corrección del Paso 5.1.
--
-- Solo las tablas y columnas que los scripts tocan. No pretende reproducir el
-- Django completo: reproducir de más haría el fixture frágil frente a
-- diferencias que a estos scripts no les importan.

CREATE TABLE auth_user (
    id           serial PRIMARY KEY,
    username     varchar(150) NOT NULL UNIQUE,
    email        varchar(254) NOT NULL DEFAULT '',
    password     varchar(128) NOT NULL DEFAULT '',
    is_active    boolean      NOT NULL DEFAULT true,
    is_staff     boolean      NOT NULL DEFAULT false,
    date_joined  timestamptz  NOT NULL DEFAULT now(),
    last_login   timestamptz
);

CREATE TABLE core_company (
    id    serial PRIMARY KEY,
    name  varchar(255) NOT NULL
);

CREATE TABLE core_clientprofile (
    id          serial PRIMARY KEY,
    user_id     integer NOT NULL REFERENCES auth_user(id),
    company_id  integer REFERENCES core_company(id)
);

-- La clave primaria es el `wr_number`, no un entero: así es en el legacy, y de
-- ahí sale que normalizarlo sea caro (lo referencian cinco tablas).
CREATE TABLE core_warehouse (
    wr_number   varchar(50) PRIMARY KEY,
    company_id  integer REFERENCES core_company(id),
    cliente_id  integer REFERENCES auth_user(id),
    status      varchar(20) NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    invoice     varchar(100),
    tracking    varchar(100),
    po          varchar(100),
    container   varchar(100)
);

CREATE TABLE core_dispatchrequest (
    id          serial PRIMARY KEY,
    method      varchar(20),
    user_id     integer REFERENCES auth_user(id),
    company_id  integer REFERENCES core_company(id),
    status      varchar(20) NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE core_dispatchrequestitem (
    id            serial PRIMARY KEY,
    dispatch_id   integer NOT NULL REFERENCES core_dispatchrequest(id),
    warehouse_id  varchar(50) REFERENCES core_warehouse(wr_number)
);

CREATE TABLE core_warehousedocument (
    id             serial PRIMARY KEY,
    warehouse_id   varchar(50) REFERENCES core_warehouse(wr_number),
    file           varchar(200),
    original_name  varchar(255),
    uploaded_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE core_piecewarehouse (
    id            serial PRIMARY KEY,
    warehouse_id  varchar(50) REFERENCES core_warehouse(wr_number),
    type_of       varchar(50),
    quantity      integer,
    description   varchar(255)
);
