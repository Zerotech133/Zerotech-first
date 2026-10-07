package com.qianjiangsoft.graphapi.aop.aspectj;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.date.DatePattern;
import cn.hutool.core.date.DateUtil;
import cn.hutool.core.text.StrPool;
import cn.hutool.core.util.ObjectUtil;
import cn.hutool.core.util.ReflectUtil;
import cn.hutool.core.util.StrUtil;
import cn.hutool.json.JSONConfig;
import cn.hutool.json.JSONUtil;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.qianjiangsoft.graphapi.aop.annotation.AsyncAnalysisTask;
import com.qianjiangsoft.graphapi.aop.annotation.SearchHistory;
import com.qianjiangsoft.graphapi.constants.AnalyseTaskStatus;
import com.qianjiangsoft.graphapi.dto.AddGraphAnalyseTaskParam;
import com.qianjiangsoft.graphapi.service.AnalysisTaskService;
import com.qianjiangsoft.graphapi.service.SearchHistoryService;
import com.qianjiangsoft.graphcore.common.redis.RedisService;
import com.qianjiangsoft.graphcore.constants.ResultCode;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.exception.BusinessException;
import com.qianjiangsoft.graphcore.model.Space;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.aspectj.lang.JoinPoint;
import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.aspectj.lang.annotation.Before;
import org.aspectj.lang.annotation.Pointcut;
import org.aspectj.lang.reflect.MethodSignature;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.annotation.Order;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;
import org.springframework.stereotype.Component;

import java.lang.reflect.Method;
import java.util.Date;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import static com.qianjiangsoft.graphapi.constants.RedisConstants.analyseTaskResultKeyExpireDays;
import static com.qianjiangsoft.graphapi.constants.RedisConstants.analyseTaskResultKeyPrefix;
import static com.qianjiangsoft.graphapi.helper.AnalysisHelper.generateAnalysisTaskName;


/**
 * @author 邓益聪
 * @date 2023/4/18 16:18
 */

@Aspect
@Slf4j
@Component
@RequiredArgsConstructor
@Order(2)
public class AsyncAnalysisTaskAspect {

    private final AnalysisTaskService analysisTaskService;

    @Autowired
    ThreadPoolTaskExecutor taskExecutor;

    @Autowired
    RedisService redisService;

    @Autowired
    private ObjectMapper objectMapper;

    @Pointcut("@annotation(com.qianjiangsoft.graphapi.aop.annotation.AsyncAnalysisTask)")
    public void pointCut(){
        //切点
    }

    /**
     *
     * @param joinPoint 切点
     */
    @Around(value = "pointCut()")
    public Object asyncAnalysis(ProceedingJoinPoint joinPoint){

        Object[] args = joinPoint.getArgs();
        if (ObjectUtil.isEmpty(args[0])) {
            log.info("入参为空");
            return null;
        }
        Method method = ((MethodSignature) joinPoint.getSignature()).getMethod();
        AsyncAnalysisTask asyncAnalysisTask = method.getAnnotation(AsyncAnalysisTask.class);

        Object arg = args[0];

        String param = "";
        //获取参数
        try {
            param = objectMapper.writeValueAsString(args[0]);
        } catch (JsonProcessingException e) {
            log.error("序列化出错", e);
        }

        log.info("AsyncAnalysisTaskAspect param json:{}", param);

        Space space = SpaceContextHolder.get();
        if (ObjectUtil.isNull(space)){
            return null;
        }

        //添加任务记录
        AddGraphAnalyseTaskParam addGraphAnalyseTaskParam = new AddGraphAnalyseTaskParam();
        Object taskName = ReflectUtil.getFieldValue(arg, "taskName");
        if(taskName == null) {
            taskName = generateAnalysisTaskName(asyncAnalysisTask.type().type);
        }
        addGraphAnalyseTaskParam.setName(taskName.toString());
        addGraphAnalyseTaskParam.setParam(param);
        addGraphAnalyseTaskParam.setType(asyncAnalysisTask.type().type);
        addGraphAnalyseTaskParam.setSpaceId(space.getId());
        Integer id = analysisTaskService.addAnalysisTask(addGraphAnalyseTaskParam);

        //执行业务代码
        Future<Integer> future = taskExecutor.submit(() -> {

            //保存上下文
            SpaceContextHolder.set(space);

            try {
                //执行controller
                Object result = joinPoint.proceed();

                //结果存入redis
                String key = analyseTaskResultKeyPrefix + id;
                redisService.set(key, result, analyseTaskResultKeyExpireDays, TimeUnit.DAYS);

                //更新执行状态
                analysisTaskService.updateAnalysisTaskStatus(id, AnalyseTaskStatus.success);

                return id;
            } catch (Throwable e) {
                log.error("aop执行出错");
                log.error(e.getMessage(), e);
                analysisTaskService.updateAnalysisTaskStatus(id, AnalyseTaskStatus.failed);
            }
            return null;
        });

        //获取返回值
        try {
            if(future.get() == null) {
                throw new BusinessException(ResultCode.BUSINESS_ERROR, "执行失败");
            }
            return analysisTaskService.queryTaskResult(id);
        } catch (Exception e) {
            log.error(e.getMessage(), e);
            throw new BusinessException(ResultCode.BUSINESS_ERROR, "获取返回值失败");
        }
    }
}
