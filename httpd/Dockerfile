FROM httpd:2.4

RUN sed -i \
  's/^#LoadModule rewrite_module/LoadModule rewrite_module/' \
  /usr/local/apache2/conf/httpd.conf

COPY httpd.conf /usr/local/apache2/conf/httpd.conf
