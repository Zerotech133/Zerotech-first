package com.qianjiangsoft.graphcore.exception;

import cn.hutool.core.util.StrUtil;
import com.qianjiangsoft.graphcore.common.Result;
import com.qianjiangsoft.graphcore.constants.ResultCode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.validation.BindException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.servlet.NoHandlerFoundException;

/**
 * 全局异常处理器
 * 
 * @author qianjiang
 */
@RestControllerAdvice
public class GlobalExceptionHandler {
	private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

	/**
	 * 业务异常
	 */
	@ExceptionHandler(BusinessException.class)
	public Result businessException(BusinessException e) {
		if (StrUtil.isEmpty(e.getCode())) {
			return Result.error(e.getMessage());
		}
		return Result.error(e.getCode(), e.getMessage());
	}

	@ExceptionHandler(NoHandlerFoundException.class)
	public Result handlerNoFoundException(Exception e) {
		log.error(e.getMessage(), e);
		return Result.error(ResultCode.HTTP_NOT_FOUND_ERROR.getCode(), "路径不存在，请检查路径是否正确");
	}

	@ExceptionHandler(Exception.class)
	public Result handleException(Exception e) {
		log.error(e.getMessage(), e);
		return Result.error(e.getMessage());
	}

	/**
	 * 自定义验证异常
	 */
	@ExceptionHandler(BindException.class)
	public Result validatedBindException(BindException e) {
		log.error(e.getMessage(), e);
		String message = e.getAllErrors().get(0).getDefaultMessage();
		return Result.error(message);
	}

	/**
	 * 自定义验证异常
	 */
	@ExceptionHandler(MethodArgumentNotValidException.class)
	public Object validExceptionHandler(MethodArgumentNotValidException e) {
		log.error(e.getMessage(), e);
		String message = e.getBindingResult().getFieldError().getDefaultMessage();
		return Result.error(message);
	}
}
