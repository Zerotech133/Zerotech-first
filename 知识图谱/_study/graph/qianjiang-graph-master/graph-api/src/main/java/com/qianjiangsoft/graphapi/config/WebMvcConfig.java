package com.qianjiangsoft.graphapi.config;

import com.qianjiangsoft.graphcore.config.ServerConfig;
import com.qianjiangsoft.graphcore.constants.Constants;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.UrlBasedCorsConfigurationSource;
import org.springframework.web.filter.CorsFilter;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.ResourceHandlerRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * 通用配置
 * 
 * @author qianjiang
 */
@Configuration
public class WebMvcConfig implements WebMvcConfigurer {

	@Autowired
	ServerConfig serverConfig;

	@Override
	public void addResourceHandlers(ResourceHandlerRegistry registry) {
		/** 本地文件上传路径 */
		registry.addResourceHandler(Constants.Config.RESOURCE_PREFIX + "/**")
				.addResourceLocations("file:" + serverConfig.getBaseDir() + "/");
	}

	/**
	 * 自定义拦截规则
	 */
	@Override
	public void addInterceptors(InterceptorRegistry registry) {

	}

	/**
	 * 跨域配置
	 */
	@Bean
	public CorsFilter corsFilter() {
		UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
		CorsConfiguration config = new CorsConfiguration();
		config.setAllowCredentials(true);
		// 设置访问源地址
		config.addAllowedOrigin("*");
		// 设置访问源请求头
		config.addAllowedHeader("*");
		// 设置访问源请求方法
		config.addAllowedMethod("*");
		// 对接口配置跨域设置
		source.registerCorsConfiguration("/**", config);
		return new CorsFilter(source);
	}
}