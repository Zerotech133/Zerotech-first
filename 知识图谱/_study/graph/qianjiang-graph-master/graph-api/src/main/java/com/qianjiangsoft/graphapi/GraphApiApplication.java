package com.qianjiangsoft.graphapi;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication(scanBasePackages = {"com.qianjiangsoft"})
@MapperScan("com.qianjiangsoft.**.mapper")
@EnableScheduling
public class GraphApiApplication {

    public static void main(String[] args) {
        try {
            SpringApplication.run(GraphApiApplication.class, args);
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    /*
     * 解决druid 日志报错：discard long time none received connection:xxx
     * */
    static {
        System.setProperty("druid.mysql.usePingMethod","false");
    }

}
