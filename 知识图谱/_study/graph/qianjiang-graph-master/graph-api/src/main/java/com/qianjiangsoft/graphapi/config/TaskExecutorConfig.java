package com.qianjiangsoft.graphapi.config;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.AsyncConfigurer;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;
import org.springframework.stereotype.Component;

import java.util.concurrent.Executor;

/**
 * @author 邓益聪
 * @date 2023/4/19 11:17
 */
@Configuration
@Component
@EnableAsync
public class TaskExecutorConfig implements AsyncConfigurer {

    @Autowired
    private ThreadPoolTaskExecutor taskExecutor;

    /**
     * Override Config
     *
     * @return
     */
    @Override
    public Executor getAsyncExecutor() {
        return taskExecutor;
    }
}
