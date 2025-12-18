
create user soliplex with password 'soliplex';
create database soliplex;
ALTER DATABASE soliplex OWNER TO soliplex;
grant all privileges on database soliplex to soliplex;
GRANT ALL PRIVILEGES ON SCHEMA public TO soliplex;


create user soliplex_eval with password 'soliplex_eval';
create database soliplex_eval;
ALTER DATABASE soliplex_eval OWNER TO soliplex_eval;
grant all privileges on database soliplex_eval to soliplex_eval;
GRANT ALL PRIVILEGES ON SCHEMA public TO soliplex_eval;

