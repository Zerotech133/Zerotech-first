package com.qianjiangsoft.graphapi.aop.aspectj;

import cn.hutool.core.util.ObjectUtil;
import cn.hutool.json.JSONConfig;
import cn.hutool.json.JSONUtil;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.qianjiangsoft.graphapi.aop.annotation.SearchHistory;
import com.qianjiangsoft.graphapi.service.SearchHistoryService;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.model.Space;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.aspectj.lang.JoinPoint;
import org.aspectj.lang.annotation.Before;
import org.aspectj.lang.annotation.Pointcut;
import org.aspectj.lang.reflect.MethodSignature;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.aspectj.lang.annotation.Aspect;

import java.lang.reflect.Method;


/**
 * @author 邓益聪
 * @date 2023/4/18 16:18
 */

@Aspect
@Slf4j
@Component
@RequiredArgsConstructor
@Order(1)
public class SearchHistoryAspect {

    private final SearchHistoryService searchHistoryService;

    @Autowired
    private ObjectMapper objectMapper;

    @Pointcut("@annotation(com.qianjiangsoft.graphapi.aop.annotation.SearchHistory)")
    public void pointCut(){
        //切点
    }

    /**
     * 在加注解的方法处记录搜索历史
     * @param joinPoint 切点
     */
    @Before(value = "pointCut()")
    public void recordHistory(JoinPoint joinPoint){
        Object[] args = joinPoint.getArgs();
        if (ObjectUtil.isEmpty(args[0])) {
            log.info("SearchHistoryAspect recordHistory 切面入参为空");
            return;
        }
        Method method = ((MethodSignature) joinPoint.getSignature()).getMethod();
        SearchHistory event = method.getAnnotation(SearchHistory.class);
        String param = "";
        try {
            param = objectMapper.writeValueAsString(args[0]);
        } catch (JsonProcessingException e) {
            log.error("序列化出错", e);
        }

        log.info("SearchHistoryAspect recordHistory param json:{}", param);

        Space space = SpaceContextHolder.get();
        if (ObjectUtil.isNull(space)){
            return;
        }

        try {
            searchHistoryService.addSearchHistory(param, event.type().type, space);
        }catch (Exception e){
            log.warn("SearchHistoryAspect recordHistory 搜索日志记录失败 ,param ：{} ； type ：{}",param,event.type());
        }
    }
}
