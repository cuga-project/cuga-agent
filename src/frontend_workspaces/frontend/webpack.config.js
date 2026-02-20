import HtmlWebpackPlugin from "html-webpack-plugin";
import CopyWebpackPlugin from "copy-webpack-plugin";
import TerserPlugin from "terser-webpack-plugin";
import webpack from "webpack";
import path from "path";
import { fileURLToPath } from "url";
import { createRequire } from "module";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const require = createRequire(import.meta.url);

// Resolve React paths dynamically so it works with or without hoisting
const reactPath = path.dirname(require.resolve("react/package.json"));
const reactDomPath = path.dirname(require.resolve("react-dom/package.json"));

// Check for the --fake-stream flag
const fakeStream = process.env.FAKE_STREAM === "true";
console.log(fakeStream);
// Base copy patterns
const baseCopyPatterns = [
  {
    from: "./static/tailwind.js",
    to: "tailwind.js",
  },
  {
    from: "./static/background.js",
    to: "background.js",
  },
  {
    from: "./static/manifest.json",
    to: "manifest.json",
  },
];

// Conditionally add fake_data.json
const copyPatterns = fakeStream
  ? [
      ...baseCopyPatterns,
      {
        from: "./static/fake_data.json",
        to: "fake_data.json",
      },
    ]
  : baseCopyPatterns;

export default {
  mode: process.env.NODE_ENV === "production" ? "production" : "development",
  entry: "./src/App.tsx",
  output: {
    path: path.resolve(__dirname, "dist"),
    filename: "[name].[contenthash].js",
    chunkFilename: "[name].[contenthash].bundle.js",
    clean: true,
    publicPath: "/",
  },
  resolve: {
    extensions: [".ts", ".tsx", ".js", ".jsx", ".css"],
    alias: {
      react: reactPath,
      "react-dom": reactDomPath,
      "@ibm/plex": path.resolve(__dirname, "node_modules/@ibm/plex"),
      "agentic_chat/CustomChat": path.resolve(__dirname, "../agentic_chat/src/CustomChat.tsx"),
      "agentic_chat/PoliciesConfig": path.resolve(__dirname, "../agentic_chat/src/PoliciesConfig.tsx"),
    },
  },
  optimization: {
    minimize: process.env.NODE_ENV === "production",
    minimizer: [
      new TerserPlugin({
        terserOptions: {
          compress: {
            drop_console: process.env.NODE_ENV === "production",
            drop_debugger: true,
            pure_funcs: process.env.NODE_ENV === "production" ? ['console.log', 'console.info'] : [],
          },
          format: {
            comments: false,
          },
        },
        extractComments: false,
      }),
    ],
    usedExports: true,
    sideEffects: false,
    splitChunks: {
      chunks: "all",
      cacheGroups: {
        vendor: {
          test: /[\\/]node_modules[\\/]/,
          name: "vendors",
          chunks: "all",
          enforce: true,
        },
      },
    },
  },
  module: {
    rules: [
      {
        test: /\.(ts|tsx|js|jsx)$/,
        exclude: /node_modules/,
        use: {
          loader: "babel-loader",
          options: {
            presets: [
              ["@babel/preset-env", { modules: false }],
              "@babel/preset-react",
              "@babel/preset-typescript"
            ],
          },
        },
      },
      {
        test: /\.css$/,
        use: ["style-loader", "css-loader"],
      },
      {
        test: /\.scss$/,
        use: [
          "style-loader",
          { loader: "css-loader", options: { url: false } },
          "sass-loader",
        ],
      },
    ],
  },
  plugins: [
    new HtmlWebpackPlugin({
      template: "./index.html",
      inject: "body",
    }),
    new CopyWebpackPlugin({
      patterns: copyPatterns,
    }),
    new webpack.DefinePlugin({
      FAKE_STREAM: JSON.stringify(fakeStream),
    }),
  ],
  devtool: process.env.NODE_ENV === "production" ? false : "source-map",
  devServer: {
    static: path.join(__dirname, "dist"),
    compress: true,
    port: 3002,
    allowedHosts: "all",
    open: true,
    hot: true,
    historyApiFallback: true,
    proxy: [
      {
        context: ['/api'],
        target: 'http://localhost:7860',
        changeOrigin: true,
        secure: false,
      },
    ],
  },
};
