package com.qianjiangsoft.graphcore.config;

import com.fasterxml.jackson.annotation.JsonAutoDetect;
import com.fasterxml.jackson.annotation.JsonTypeInfo;
import com.fasterxml.jackson.annotation.PropertyAccessor;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.jsontype.impl.LaissezFaireSubTypeValidator;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.data.redis.serializer.GenericJackson2JsonRedisSerializer;
import org.springframework.data.redis.serializer.RedisSerializer;
import org.springframework.data.redis.serializer.StringRedisSerializer;

/**
 * redis配置
 * 
 * @author qianjiang
 */
@Configuration
public class RedisConfig {

	/**
	 * 自定义redis序列化的机制,重新定义一个ObjectMapper.防止和MVC的冲突
	 *
	 * @return
	 */
	@Bean
	public RedisSerializer<Object> redisSerializer() {

		ObjectMapper objectMapper = new ObjectMapper();

		// 指定要序列化的域，field,get和set,以及修饰符范围，ANY是都有包括private和public
		objectMapper.setVisibility(PropertyAccessor.ALL, JsonAutoDetect.Visibility.ANY);
		//序列化时候遇到空对象不抛出异常
		objectMapper.configure(SerializationFeature.FAIL_ON_EMPTY_BEANS, false);
		//反序列化时遇到未知属性不报错
		objectMapper.configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false);
		//不使用默认的dateTime进行序列化,
		objectMapper.configure(SerializationFeature.WRITE_DATE_KEYS_AS_TIMESTAMPS, false);
		//使用JSR310提供的序列化类,里面包含了大量的JDK8时间序列化类
		objectMapper.registerModule(new JavaTimeModule());
		//启用反序列化所需的类型信息,在属性中添加@class
		objectMapper.activateDefaultTyping(LaissezFaireSubTypeValidator.instance, ObjectMapper.DefaultTyping.NON_FINAL, JsonTypeInfo.As.PROPERTY);
		//配置null值的序列化器
		GenericJackson2JsonRedisSerializer.registerNullValueSerializer(objectMapper, null);
		return new GenericJackson2JsonRedisSerializer(objectMapper);
	}

	@Bean
	public RedisTemplate<String, Object> redisTemplate(RedisConnectionFactory redisConnectionFactory,
													   RedisSerializer<Object> redisSerializer) {
		RedisTemplate<String, Object> template = new RedisTemplate<>();
		template.setConnectionFactory(redisConnectionFactory);

		template.setDefaultSerializer(redisSerializer);

		template.setKeySerializer(StringRedisSerializer.UTF_8);
		template.setValueSerializer(redisSerializer);

		template.setHashKeySerializer(StringRedisSerializer.UTF_8);
		template.setHashValueSerializer(redisSerializer);
		template.afterPropertiesSet();
		return template;
	}
}
