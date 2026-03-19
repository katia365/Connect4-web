-- PostgreSQL schema — Connect4
-- Nettoyé pour compatibilité Render (PostgreSQL 16)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

-- TABLE parties
CREATE TABLE IF NOT EXISTS public.parties (
    id integer NOT NULL,
    date_debut timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    date_fin timestamp without time zone,
    sequence_coups text NOT NULL,
    sequence_miroir text NOT NULL,
    mode_jeu integer NOT NULL,
    dimensions character varying(10) NOT NULL,
    statut character varying(20) NOT NULL,
    vainqueur character varying(20),
    source character varying(20) NOT NULL,
    coup_cursor integer DEFAULT 0,
    CONSTRAINT parties_mode_jeu_check CHECK ((mode_jeu = ANY (ARRAY[0, 1, 2]))),
    CONSTRAINT parties_source_check CHECK (((source)::text = ANY ((ARRAY['LOCALE'::character varying, 'IMPORT_TXT'::character varying, 'IMPORT_BGA'::character varying])::text[]))),
    CONSTRAINT parties_statut_check CHECK (((statut)::text = ANY ((ARRAY['EN_COURS'::character varying, 'TERMINEE'::character varying, 'ABANDONNEE'::character varying])::text[]))),
    CONSTRAINT parties_vainqueur_check CHECK (((vainqueur)::text = ANY ((ARRAY['ROUGE'::character varying, 'JAUNE'::character varying, 'MATCH_NUL'::character varying, 'NONE'::character varying])::text[])))
);

CREATE SEQUENCE IF NOT EXISTS public.parties_id_seq
    AS integer START WITH 1 INCREMENT BY 1
    NO MINVALUE NO MAXVALUE CACHE 1;

ALTER SEQUENCE public.parties_id_seq OWNED BY public.parties.id;
ALTER TABLE ONLY public.parties ALTER COLUMN id SET DEFAULT nextval('public.parties_id_seq'::regclass);

-- TABLE situation
CREATE TABLE IF NOT EXISTS public.situation (
    id integer NOT NULL,
    id_partie integer NOT NULL,
    coup_index integer NOT NULL,
    sequence_prefix text NOT NULL,
    player_to_move character varying(10) NOT NULL,
    CONSTRAINT situation_player_to_move_check CHECK (((player_to_move)::text = ANY ((ARRAY['ROUGE'::character varying, 'JAUNE'::character varying])::text[])))
);

CREATE SEQUENCE IF NOT EXISTS public.situation_id_seq
    AS integer START WITH 1 INCREMENT BY 1
    NO MINVALUE NO MAXVALUE CACHE 1;

ALTER SEQUENCE public.situation_id_seq OWNED BY public.situation.id;
ALTER TABLE ONLY public.situation ALTER COLUMN id SET DEFAULT nextval('public.situation_id_seq'::regclass);

-- CONTRAINTES
ALTER TABLE ONLY public.parties ADD CONSTRAINT parties_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.parties ADD CONSTRAINT parties_sequence_coups_key UNIQUE (sequence_coups);
ALTER TABLE ONLY public.parties ADD CONSTRAINT parties_sequence_miroir_key UNIQUE (sequence_miroir);
ALTER TABLE ONLY public.situation ADD CONSTRAINT situation_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.situation ADD CONSTRAINT situation_id_partie_coup_index_key UNIQUE (id_partie, coup_index);
ALTER TABLE ONLY public.situation ADD CONSTRAINT situation_id_partie_fkey FOREIGN KEY (id_partie) REFERENCES public.parties(id) ON DELETE CASCADE;

-- INDEX
CREATE INDEX IF NOT EXISTS idx_parties_mode    ON public.parties USING btree (mode_jeu);
CREATE INDEX IF NOT EXISTS idx_parties_source  ON public.parties USING btree (source);
CREATE INDEX IF NOT EXISTS idx_parties_statut  ON public.parties USING btree (statut);
CREATE INDEX IF NOT EXISTS idx_situation_partie ON public.situation USING btree (id_partie);
