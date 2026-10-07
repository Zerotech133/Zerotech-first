const {createProxyMiddleware} = require('http-proxy-middleware')
module.exports = function (app) {
  console.log(1)
    app.use(createProxyMiddleware('/api', {
        // target: 'http://127.0.0.1:9999',
         target: 'http://192.168.30.104:9001',
        //target: 'http://192.168.1.5:8080',
        // target: 'http://192.168.1.4:9999',
        // target: 'http://192.168.30.101:12202',
        pathRewrite: {
            '^/api': ''
            }
        }
    ))
}
